"""직접 게시한 공지에 한해 더 충실한 원문을 찾아 보강한다."""
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import tempfile
from bs4 import BeautifulSoup

from .events import Notice, compare_notices, quality_details
from .post_editor import PlatoPostEditor, PostEditError, validate_article_url, MAX_OPERATION_BYTES
from .text import clean_display_title, normalize_title
from .quality import display_title,inspect_rendered_post,PostVerificationError,sanitize_html


def as_notice(announcement, department):
    metadata = getattr(announcement, 'metadata', {}) or {}
    return Notice(title=getattr(announcement, 'original_title', announcement.title),
                  observed_at=datetime.now().isoformat(timespec='seconds'), source_url=announcement.url,
                  department=department, body_text=(announcement.content_text or '')[:6000],
                  attachment_urls=[item['url'] for item in metadata.get('attachment_links', [])],
                  metadata_complete=bool(metadata.get('attachment_scan_complete')),
                  publication_time=metadata.get('publication_time') or '')


def adds_information(old, new):
    a, b = quality_details(old), quality_details(new)
    old_fields = {key for key, value in a['information_fields'].items() if value}
    new_fields = {key for key, value in b['information_fields'].items() if value}
    # 오래된 정보를 지우지 않고 추가하더라도, 명백한 일정 충돌은 자동 처리하지 않는다.
    dates = lambda text: set(re.findall(r'(?<!\d)(?:20\d{2}[.\-/]\s*)?\d{1,2}[./월]\s*\d{1,2}(?:일)?', text))
    old_dates, new_dates = dates(old.body_text), dates(new.body_text)
    if old_dates and new_dates and not old_dates <= new_dates:
        return False
    return (bool(new_fields - old_fields)
            or bool(set(new.attachment_urls) - set(old.attachment_urls))
            or (b['body_characters'] >= a['body_characters'] + max(200, int(a['body_characters'] * .2))))


def enriched_subject(current, title, board):
    title = display_title(clean_display_title(title))
    if current.startswith('⭐'):
        return '⭐' + title.lstrip('⭐')
    if board in ('기타 공지사항', '장학금'):
        prefix = re.match(r'^\[[^\]]+\]\s*', current)
        if prefix and not title.startswith(prefix.group(0).strip()):
            return prefix.group(0) + title
    return title


def merged_body(current_html, announcement, old_notice):
    link = '<p>추가 원문: <a href="{0}">{0}</a></p>'.format(escape(announcement.url, quote=True))
    old_text = re.sub(r'\W+', '', normalize_title(old_notice.body_text))
    new_text = re.sub(r'\W+', '', normalize_title(announcement.content_text))
    if new_text and new_text == old_text:
        return current_html + '<hr>' + link
    # 기존 내용과 링크를 보존해 원문 선택 때문에 정보가 사라지지 않게 한다.
    return current_html + '<hr><p><strong>추가 안내</strong></p>' + link + sanitize_html(announcement.content_html,announcement.url)


class PublishedNoticeStore:
    def __init__(self, base_dir):
        self.root = Path(base_dir) / '.runtime/intelligence'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'published-posts.json'

    def load(self):
        if not self.path.exists(): return []
        if self.path.stat().st_size > 16 * 1024 * 1024: raise PostEditError('Published index exceeds limit')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema') != 1: raise PostEditError('Unsupported published index')
        return data['posts']

    def put(self, record):
        posts = [row for row in self.load() if row['article_url'] != record['article_url']]
        posts.append(record)
        cutoff = (datetime.now() - timedelta(days=14)).isoformat()
        posts = [row for row in posts if row['notice']['observed_at'] >= cutoff][-1000:]
        value = json.dumps({'schema':1, 'posts':posts}, ensure_ascii=False)
        if len(value.encode()) > 16 * 1024 * 1024: raise PostEditError('Published index exceeds limit')
        temporary = self.path.with_suffix('.pending')
        temporary.write_text(value, encoding='utf-8'); os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def find(self, announcement, department):
        candidate = as_notice(announcement, department)
        matches = []
        for record in self.load():
            if (record.get('disabled') or announcement.url in record['sources']
                    or announcement.url in record.get('ignored_sources',[]) or record.get('updates',0) >= 3): continue
            old = Notice(**record['notice'])
            if compare_notices(candidate, old)['relation'] == 'same_event' and adds_information(old, candidate):
                matches.append(record)
        return matches[0] if len(matches) == 1 else None


def enabled():
    return os.getenv('NOTICE_ENRICHMENT_MODE', 'off') == 'enabled'


def find_target(announcement, department, base_dir):
    if not enabled(): return None
    try:
        return PublishedNoticeStore(base_dir).find(announcement, department)
    except Exception as exc:
        print(f'보강 후보 조회 생략: {type(exc).__name__}', flush=True)
        return None


def register_post(service, announcement, department, receipt, base_dir):
    if not enabled() or not isinstance(receipt, dict) or not isinstance(receipt.get('article_url'),str): return
    try:
        validate_article_url(receipt['article_url'])
        editor = PlatoPostEditor(service, Path(base_dir) / '.runtime/intelligence/edit-backups')
        snapshot = receipt.get('_snapshot') or editor.read(receipt['article_url'])
        links = {node['href'] for node in BeautifulSoup(snapshot['body_html'],'html.parser').find_all('a',href=True)}
        if snapshot['subject'] != receipt.get('subject') or receipt.get('source_url') != announcement.url or announcement.url not in links:
            raise PostEditError('Published article receipt mismatch')
        PublishedNoticeStore(base_dir).put({'article_url':receipt['article_url'], 'signature':snapshot['signature'],
            'notice':asdict(as_notice(announcement, department)), 'board':announcement.notice_board_name,
            'sources':[announcement.url], 'departments':[department], 'updates':0,
            'file_hashes':{item['name']:item['sha256'] for item in snapshot['files']}})
    except Exception as exc:
        # 게시 성공 뒤 색인 실패가 발생해도 같은 공지를 다시 쓰지 않는다.
        print(f'보강 대상 등록 생략: {type(exc).__name__}', flush=True)


def apply_enrichment(service, record, announcement, department, base_dir):
    editor = PlatoPostEditor(service, Path(base_dir) / '.runtime/intelligence/edit-backups')
    current = editor.read(record['article_url'])
    if current['signature'] != record['signature']:
        PublishedNoticeStore(base_dir).put(dict(record,disabled=True))
        print('직접 수정한 공지는 자동 보강을 생략합니다.', flush=True)
        return None
    old_notice = Notice(**record['notice'])
    new_notice = as_notice(announcement, department)
    if compare_notices(old_notice, new_notice)['relation'] != 'same_event' or not adds_information(old_notice,new_notice):
        PublishedNoticeStore(base_dir).put(dict(record,ignored_sources=list(dict.fromkeys(record.get('ignored_sources',[])+[announcement.url]))[-10:]))
        return None
    # 내용 해시가 같은 파일은 다시 올리지 않는다. 같은 이름의 다른 파일은 이름을 분리한다.
    known_hashes = {item['sha256'] for item in current['files']}
    known_names = {item['name'] for item in current['files']}
    folder_root = Path(base_dir) / '.runtime/intelligence'
    if sum(Path(value).stat().st_size for value in announcement.files) > MAX_OPERATION_BYTES:
        raise PostEditError('Enrichment attachments exceed limit')
    with tempfile.TemporaryDirectory(prefix='upload-', dir=folder_root) as temporary:
        uploads = []
        for value in announcement.files:
            path = Path(value)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in known_hashes: continue
            name = path.name
            if name in known_names: name = digest[:12] + '-' + name
            if name in known_names: raise PostEditError('Conflicting upload filename')
            destination = Path(temporary) / name
            destination.write_bytes(path.read_bytes()); os.chmod(destination,0o600)
            uploads.append(destination); known_hashes.add(digest); known_names.add(name)
        subject = enriched_subject(current['subject'], announcement.title, record['board'])
        if not uploads and normalize_title(announcement.content_text) == normalize_title(old_notice.body_text) and subject == current['subject']:
            record = dict(record, sources=list(dict.fromkeys(record['sources'] + [announcement.url])))
            PublishedNoticeStore(base_dir).put(record)
            return False
        body = merged_body(current['body_html'], announcement, old_notice)
        if len(body.encode()) > 300000: raise PostEditError('Enriched body exceeds limit')
        result = editor.update(record['article_url'], subject, body, uploads, expected_signature=record['signature'])
        if os.getenv('POST_VALIDATION_MODE','off')=='enabled':
            service.safe_get(record['article_url'],login_required=True)
            expected_text=BeautifulSoup(body,'html.parser').get_text(' ',strip=True)
            rendered=inspect_rendered_post(service.driver,subject,expected_text,announcement.url)
            if any(reason!='image_load_unconfirmed' for reason in rendered['reasons']):
                editor.restore(result)
                PublishedNoticeStore(base_dir).put(dict(record,ignored_sources=list(dict.fromkeys(record.get('ignored_sources',[])+[announcement.url]))[-10:]))
                raise PostVerificationError('Enriched post failed display validation and was restored')
    record = dict(record, signature=result['after']['signature'], notice=asdict(new_notice),
                  sources=list(dict.fromkeys(record['sources'] + [announcement.url])),
                  departments=sorted(set(record['departments'] + [department])), updates=record['updates']+1,
                  file_hashes={item['name']:item['sha256'] for item in result['after']['files']})
    PublishedNoticeStore(base_dir).put(record)
    print('공지 보강 완료: 제목·본문·기존 및 새 첨부파일 검증', flush=True)
    return True
