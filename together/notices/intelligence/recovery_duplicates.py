"""대체 중복 판단의 누락 복구와 자료를 보존한 중복 글 정리."""
import json
from bs4 import BeautifulSoup
from together.storage.pending_notices import PendingNotices, notice_identity
from .publication import known_receipt, remember_receipt
from .enrichment import PublishedNoticeStore
from .post_editor import PlatoPostEditor


def correct_duplicate(service, base_dir, row, reviewed):
    from .recovery import _delete_once
    source = json.loads(row['context']).get('source_url', '')
    local = json.loads(row['local_result'])
    receipt = known_receipt(base_dir, source)
    if local['relation'] == 'same_event' and reviewed['relation'] != 'same_event':
        if receipt and receipt.get('state') == 'verified':
            return {'done': True, 'reason': 'existing_post_verified'}
        from together.crawler.pages import PageUrlManager
        payload = json.loads(json.loads(row['request'])['messages'][-1]['content'])
        candidate = payload.get('candidates', [{}])[local['matched_index']]
        pending = PendingNotices(base_dir)
        # 당시 원문과 실제 목록의 소속을 연결한다. CSV 번호를 되돌리지 않는다.
        for page in PageUrlManager().announcement_pages:
            context = json.loads(row['context'])
            if page.department != context.get('department'):
                continue
            from urllib.parse import urlsplit, parse_qs
            parts = urlsplit(source); query = parse_qs(parts.query)
            board_parts = urlsplit(page.source_url)
            if parts.netloc != board_parts.netloc:
                continue
            if '/CMS/Board/' in parts.path and query.get('page', ['1'])[0] != parse_qs(board_parts.query).get('page', ['1'])[0]:
                continue
            pending.add(page.source_url, source, page.department)
            pending.mark_recovery(page.source_url, source, candidate.get('title', payload['candidate_titles'][local['matched_index']]))
            return {'done': False, 'reason': 'missing_post_requeued', 'source_url': source}
        return {'done': False, 'reason': 'source_board_mapping_unconfirmed'}
    if reviewed['relation'] != 'same_event':
        return {'done': True, 'reason': 'both_decisions_preserve_separate_notice'}
    if not receipt:
        return {'done': True, 'reason': 'no_new_post_to_remove'}
    if receipt.get('duplicate_of'):
        return {'done': True, 'reason': 'duplicate_already_reconciled'}
    payload = json.loads(json.loads(row['request'])['messages'][-1]['content'])
    candidates = payload.get('candidates', [])
    if not candidates:
        return {'done': False, 'reason': 'candidate_source_missing'}
    target_source = candidates[reviewed['matched_index']].get('source_url')
    index = PublishedNoticeStore(base_dir)
    posts = index.load()
    target = next((p for p in posts if target_source in p.get('sources', []) and p['article_url'] != receipt['article_url'] and not p.get('disabled')), None)
    own = next((p for p in posts if p['article_url'] == receipt['article_url'] and not p.get('disabled')), None)
    if not own or not target:
        return {'done': False, 'reason': 'both_original_post_proofs_required'}
    editor = PlatoPostEditor(service, index.root / 'recovery/backups')
    current, representative = editor.read(own['article_url'], backup=True), editor.read(target['article_url'], backup=True)
    if current['signature'] != own['signature'] or representative['signature'] != target['signature']:
        return {'done': False, 'reason': 'manual_edit_protected'}
    def body_material(value):
        soup = BeautifulSoup(value, 'html.parser')
        for node in soup.find_all('p'):
            if node.get_text(' ', strip=True).startswith('본문 링크 :'):
                node.decompose()
        return ''.join(soup.get_text(' ', strip=True).split()), {n.get('src') for n in soup.find_all('img')}
    if body_material(current['body_html']) != body_material(representative['body_html']):
        return {'done': False, 'reason': 'different_body_material_preserved_for_enrichment'}
    a, b = ({v['sha256'] for v in snapshot['files']} for snapshot in (current, representative))
    if not a <= b:
        return {'done': False, 'reason': 'unique_attachments_preserved'}
    # 접수/삭제 이력은 별도 JSON으로 보존하며 학교 조회 후에만 재시도한다.
    from pathlib import Path
    from .recovery import _save
    path = index.root / 'recovery' / (row['key'] + '-duplicate.json')
    state = {'source_url': source, 'original': current, 'representative': representative}
    def checkpoint(phase):
        _save(path, dict(state, phase=phase))
    checkpoint('backed_up')
    _delete_once(service._http, service._http.board_url(own['board']), own['article_url'], current['subject'], checkpoint)
    remember_receipt(base_dir, dict(receipt, article_url=target['article_url'], board=target['board'], duplicate_of=own['article_url']))
    index.put(dict(own, disabled=True, duplicate_of=target['article_url']))
    index.put(dict(target, sources=list(dict.fromkeys(target['sources'] + [source]))))
    checkpoint('complete')
    return {'done': True, 'reason': 'identical_duplicate_removed_material_preserved', 'subscriber_resends': 0}
