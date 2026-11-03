"""대체 판단을 실제 API로 재검토하고, 검증할 수 있는 수정만 재개한다."""
import csv
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
from bs4 import BeautifulSoup
from together.storage.fallback_reviews import FallbackReviews
from together.storage.pending_notices import PendingNotices
from together.operations.events import clear_notice, set_notice, event
from .publication import known_receipt, remember_receipt
from .post_editor import PlatoPostEditor, PostEditError, validate_article_url
from .enrichment import PublishedNoticeStore


def _links(client, board, subject):
    """공식 제목 검색의 끝까지 읽어 불확실한 POST 결과부터 대조한다."""
    first = board + '&' + urlencode({'keyfield': 'subject', 'keyword': subject.lstrip('⭐')})
    pending, seen, found = [first], set(), {}
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        with client.get(url, timeout=(5, 30)) as response:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
        for node in soup.select('a[href]'):
            link = urljoin(url, node['href'])
            parts = urlsplit(link)
            if parts.netloc != 'dev-plato.pusan.ac.kr':
                continue
            if parts.path == '/mod/ubboard/article.php' and parse_qs(parts.query).get('bwid'):
                found[parse_qs(parts.query)['bwid'][0]] = link
            elif node.find_parent(class_=re.compile(r'pag|paging')) and parts.path == '/mod/ubboard/view.php':
                if parse_qs(parts.query).get('id') == parse_qs(urlsplit(board).query).get('id') and link not in seen:
                    pending.append(link)
    return found


def _save(path, value):
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.chmod(0o600)
    os.replace(temp, path)


def _delete_once(client, board, article, subject, checkpoint):
    post = validate_article_url(article)['post_id']
    if post not in _links(client, board, subject):
        return
    with client.get(board, timeout=(5, 30)) as response:
        soup = BeautifulSoup(response.text, 'html.parser')
    form = soup.select_one('#ubboard_list_form')
    if not form or not form.select_one('input[name="manage[]"][value="' + post + '"]'):
        # 검색 결과의 해당 페이지에서 다시 폼을 읽는다. 첫 페이지로 한정하지 않는다.
        search = board + '&' + urlencode({'keyfield': 'subject', 'keyword': subject.lstrip('⭐')})
        with client.get(search, timeout=(5, 30)) as response:
            form = BeautifulSoup(response.text, 'html.parser').select_one('#ubboard_list_form')
    if not form or not form.select_one('input[name="manage[]"][value="' + post + '"]'):
        raise PostEditError('Deletion target not verified on school form')
    data = client._form_values(form)
    data['manage[]'], data['type'] = post, 'check_delete'
    action = urljoin(board, form.get('action'))
    if urlsplit(action).path != '/mod/ubboard/action.php':
        raise PostEditError('Unexpected deletion endpoint')
    checkpoint('delete_submitting')
    with client.post_once(action, data=data, timeout=(5, 45)) as response:
        response.raise_for_status()
    if post in _links(client, board, subject):
        raise PostEditError('Deletion not confirmed; reconcile before retry')


def _same_material(a, b):
    return a['subject'] == b['subject'] and a['body_html'] == b['body_html'] and a['notice'] == b['notice'] and {v['name']: v['sha256'] for v in a['files']} == {v['name']: v['sha256'] for v in b['files']}


def move_category(service, base_dir, source, category, key):
    """백업→학교 복사→내용/첨부 검증→기존글 삭제. 알림 재전송 없음."""
    receipt = known_receipt(base_dir, source)
    if not receipt or receipt.get('state') != 'verified':
        return {'done': False, 'reason': 'verified_receipt_required'}
    target = '기타 공지사항' if category == '해당없음' else category
    root = Path(base_dir) / '.runtime/intelligence/recovery'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (key + '.json')
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'phase': 'prepared'}
    if receipt.get('board') == target and not path.exists():
        return {'done': True, 'reason': 'already_in_correct_board'}
    receipt = state.get('original_receipt', receipt)
    def checkpoint(phase, **kwargs):
        state.update(phase=phase, **kwargs)
        _save(path, state)
    if state['phase'] == 'complete':
        return {'done': True, 'reason': 'already_corrected'}
    editor = PlatoPostEditor(service, root / 'backups')
    client = service._http
    if client is None:
        return {'done': False, 'reason': 'authenticated_http_required'}
    oldurl = receipt['article_url']
    oldid = validate_article_url(oldurl)['post_id']
    oldboard, newboard = client.board_url(receipt['board']), client.board_url(target)
    index = PublishedNoticeStore(base_dir)
    record = next((p for p in index.load() if p['article_url'] == oldurl), None)
    if state['phase'] == 'prepared':
        original = editor.read(oldurl, backup=True)
        if not record or original['signature'] != record['signature'] or source not in original['body_html']:
            return {'done': False, 'reason': 'manual_edit_or_missing_original_proof'}
        checkpoint('backed_up', original=original, original_record=record, original_receipt=receipt)
    original = state['original']
    # 재개할 때도 실제 복사본부터 찾는다. 접수 불명이면 다시 요청하지 않는다.
    matches = []
    for _, link in _links(client, newboard, original['subject']).items():
        copied = editor.read(link, backup=True)
        if _same_material(original, copied):
            matches.append((link, copied))
    if len(matches) > 1:
        return {'done': False, 'reason': 'multiple_existing_target_posts'}
    if not matches:
        if state['phase'] not in ('backed_up',):
            return {'done': False, 'reason': 'copy_acceptance_unknown_original_preserved'}
        current = editor.read(oldurl)
        if current['signature'] != original['signature']:
            return {'done': False, 'reason': 'manual_edit_after_backup'}
        with client.get(oldboard, timeout=(5, 30)) as response:
            soup = BeautifulSoup(response.text, 'html.parser')
        share = soup.select_one('.btn-share')
        if not share:
            raise PostEditError('School copy form unavailable')
        with client.get(urljoin(oldboard, share['href']) + '&bwids=' + oldid, timeout=(5, 30)) as response:
            form = BeautifulSoup(response.text, 'html.parser').select_one('#share_confirm_form')
        data = client._form_values(form)
        option = next(o for o in form.select('select[name="select_course_board_157301"] option') if o.get_text(' ', strip=True) == target)
        checkbox = form.select_one('input[name="select_course_157301"]')
        data[checkbox['name']] = checkbox.get('value', 'on')
        data['select_course_board_157301'] = option['value']
        if data.get('share_bwids') != oldid:
            raise PostEditError('Copy target mismatch')
        action = urljoin(oldboard, form.get('action'))
        if urlsplit(action).path != '/mod/ubboard/action.php':
            raise PostEditError('Unexpected copy endpoint')
        checkpoint('copy_submitting')
        with client.post_once(action, data=data, timeout=(5, 45)) as response:
            response.raise_for_status()
        # 다음 호출은 조회부터 하므로 여기서 장애가 나도 중복 복사하지 않는다.
        return move_category(service, base_dir, source, category, key)
    newurl, copied = matches[0]
    checkpoint('copy_verified', newurl=newurl, copied=copied)
    if '/mod_ubboard/attachment/' + oldid + '/' in copied['body_html']:
        return {'done': False, 'reason': 'embedded_old_attachment_dependency_original_preserved'}
    if oldid in _links(client, oldboard, original['subject']):
        if editor.read(oldurl)['signature'] != original['signature']:
            return {'done': False, 'reason': 'manual_edit_before_delete'}
        _delete_once(client, oldboard, oldurl, original['subject'], checkpoint)
    if not _same_material(original, editor.read(newurl)):
        raise PostEditError('Target changed after copy; inspect before finalization')
    remember_receipt(base_dir, dict(receipt, article_url=newurl, board=target, moved_from=oldurl))
    oldrecord = state['original_record']
    index.put(dict(oldrecord, disabled=True, moved_to=newurl))
    index.put(dict(oldrecord, article_url=newurl, board=target, signature=copied['signature']))
    path_csv = Path(base_dir) / 'sorted_labeled_data.csv'
    if path_csv.exists():
        backup = root / (key + '-labels.csv')
        if not backup.exists():
            backup.write_bytes(path_csv.read_bytes()); backup.chmod(0o600)
        rows = list(csv.reader(io.StringIO(path_csv.read_text(encoding='utf-8-sig'))))
        title = oldrecord['notice']['title']
        for row in rows:
            if len(row) == 2 and row[0] == title:
                row[1] = category
        output = io.StringIO(); csv.writer(output, lineterminator='\n').writerows(rows)
        temporary = path_csv.with_suffix('.review-pending'); temporary.write_text(output.getvalue(), encoding='utf-8'); os.replace(temporary, path_csv)
    checkpoint('complete')
    return {'done': True, 'reason': 'category_corrected', 'article_url': newurl, 'subscriber_resends': 0}


def review_pending(service, base_dir):
    from together.storage.api_usage import ApiAccounting
    store = FallbackReviews(base_dir)
    for row in store.pending():
        from together.integrations.openai_client import _decision
        context, request, local = (json.loads(row[name]) for name in ('context', 'request', 'local_result'))
        try:
            result = json.loads(row['reviewed_result']) if row['reviewed_result'] else _decision(row['model'], request, require_remote=True)
            ApiAccounting(base_dir).store(row['model'], request, result)
        except Exception:
            break  # 장애 중에도 이후의 공지 수집은 계속한다.
        task = request['response_format']['json_schema']['name']
        field = 'category' if task == 'notice_category' else 'relation'
        if result[field] == local[field]:
            store.update(row['key'], 'verified', result, {'reason': 'remote_agrees'})
            continue
        store.update(row['key'], 'correction_pending', result)
        try:
            set_notice(context.get('source_url', ''), context.get('department', ''), context.get('title', ''))
            if task == 'notice_category' and result['category'] != '판단보류':
                action = move_category(service, base_dir, context.get('source_url', ''), result['category'], row['key'])
            else:
                # 동일성 오판은 본문/기존 게시물 연결까지 확인한 뒤 수정한다.
                from .recovery_duplicates import correct_duplicate
                action = correct_duplicate(service, base_dir, row, result)
            store.update(row['key'], 'corrected' if action.get('done') else 'correction_pending', result, action)
            event('재검토', '로컬 대체 판단 재검토 · ' + ('수정 완료' if action.get('done') else '보존 후 추가 확인'), outcome='corrected' if action.get('done') else 'correction_pending', console=False)
        except Exception as exc:
            store.update(row['key'], 'correction_pending', result, {'error': type(exc).__name__})
        finally:
            clear_notice()
