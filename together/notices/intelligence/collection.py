"""수집 양식과 관계없이 학습에 필요한 원문을 별도로 보존한다.

게시·발송·진행 번호는 건드리지 않는다. 문서나 이미지를 추가로 내려받지
않고, 이미 받은 본문과 파일만 기록한다. 누락 필드는 추측으로 채우지 않는다.
"""
from datetime import datetime
from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from bs4 import BeautifulSoup
from .focus import exchange_focus
from .text import identity


def content_signals(html, text):
    soup = BeautifulSoup(html or '', 'html.parser')
    return {'table_count': len(soup.select('table')),
            'inline_image_count': len(soup.select('img')),
            'body_characters': len(text or ''),
            'body_text_missing': not (text or '').strip() or text == '내용 없음'}


def _safe_html(html):
    soup = BeautifulSoup(html or '', 'html.parser')
    for node in soup.select('script,style,form,input,textarea,button,iframe'):
        node.decompose()
    for node in soup.find_all(True):
        node.attrs = {k:v for k,v in node.attrs.items()
                      if k in ('href','src','alt','title','class','id','colspan','rowspan')}
    return str(soup)


def _blob(root, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    key = hashlib.sha256(raw).hexdigest()
    path = root / 'bodies' / key[:2] / (key + '.json.gz')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        # 서로 같은 본문은 한 번만 보관한다. 중간 파일은 원문으로 취급하지 않는다.
        fd, name = tempfile.mkstemp(prefix=key + '.', suffix='.pending', dir=path.parent)
        pending = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(gzip.compress(raw, mtime=0))
            pending.chmod(0o600)
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
    return key, str(path.relative_to(root))


def record_collection(base_dir, announcement, department, phase='observed'):
    root = Path(base_dir) / '.runtime/intelligence/collection'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    body = announcement.content_text or ''
    title = getattr(announcement, 'original_title', announcement.title)
    metadata = dict(getattr(announcement, 'metadata', {}) or {})
    signals = content_signals(announcement.content_html, body)
    fields = {'title_identity': identity(title), 'exchange_study': exchange_focus(title, body),
              'event_name_evidence': {'original_title':title,'provenance':'source_title_not_inferred'},
              'organization': {'value':department,'provenance':'configured_department'}}
    # 원래 행/표의 문구를 보관해 나중에 추출기를 개선할 수 있다. 정답으로 사용하지 않는다.
    import re
    text_lines = BeautifulSoup(announcement.content_html or '', 'html.parser').get_text('\n', strip=True).splitlines()
    fields['labelled_evidence'] = [line for line in text_lines if re.search(
        r'신청\s*(?:대상|기간|자격)|지원\s*(?:대상|자격)|참가\s*대상|모집\s*(?:대상|기간)|행사\s*(?:명|일시)|접수\s*기간|일\s*시\s*[:：]|장\s*소\s*[:：]', line)]
    parsed = BeautifulSoup(announcement.content_html or '', 'html.parser')
    candidates = list(dict.fromkeys(text_lines + [node.get_text(' ',strip=True) for node in parsed.select('tr,li,dd,p')]))
    labels = {'eligibility':r'(?:신청|지원|참가|모집|참여)\s*(?:대상|자격)|지원\s*조건',
              'schedule':r'(?:신청|모집|접수|행사|교육)\s*(?:기간|일시)|일\s*시\s*[:：]|마감',
              'event_name':r'(?:행사|프로그램|사업)\s*명\s*[:：]',
              'organizer':r'주\s*(?:최|관)\s*[:：]'}
    fields['body_evidence'] = {key:[{'text':line,'provenance':'body_label_or_table_row'} for line in candidates if re.search(pattern,line)] for key,pattern in labels.items()}
    attachments = []
    for value in announcement.files:
        path = Path(value)
        if not path.is_file():
            attachments.append({'name':path.name,'status':'local_file_unavailable'})
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024), b''):digest.update(chunk)
        attachments.append({'name':path.name,'bytes':path.stat().st_size,
                            'sha256':digest.hexdigest(),'status':'already_downloaded'})
    missing = []
    if not metadata.get('publication_time_is_explicit'):missing.append('original_publication_date')
    if signals['body_text_missing']:missing.append('body_text')
    if not metadata.get('attachment_scan_complete'):missing.append('attachment_list_completeness')
    if not fields['labelled_evidence']:missing.append('labelled_schedule_or_eligibility')
    if metadata.get('layout') in (None, 'unknown'):missing.append('recognized_article_layout')
    payload = {'schema':1,'title':title,'body_text':body,'body_html':_safe_html(announcement.content_html),
               'metadata':metadata,'signals':signals,'fields':fields,'attachments':attachments,
               'missing_fields':missing,'no_additional_downloads':True}
    sha, relative = _blob(root, payload)
    db_path = root / 'index.sqlite3'
    with closing(sqlite3.connect(db_path, timeout=2)) as db, db:
        db_path.chmod(0o600)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS samples (source_url TEXT, department TEXT, phase TEXT, sha256 TEXT, artifact TEXT, first_seen TEXT, last_seen TEXT, observations INTEGER, PRIMARY KEY(source_url,department,phase,sha256))')
        now = datetime.now().astimezone().isoformat()
        db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(source_url,department,phase,sha256) DO UPDATE SET last_seen=excluded.last_seen,observations=observations+1',
                   (announcement.url,department,phase,sha,relative,now,now))
    return {'sha256':sha,'artifact':relative,'missing_fields':missing}


def observe_collection(announcement, department, base_dir, phase='observed'):
    try:
        return record_collection(base_dir, announcement, department, phase)
    except Exception as exc:
        from together.operations.events import event
        event('검증','학습용 원문 보존 실패',console=False,error=type(exc).__name__,source_url=getattr(announcement,'url',''))
        return None


def preserve_unknown_article(soup, source_url, base_dir):
    """게시할 수 없는 구조도 진단 자료로만 보관한다. 로그인 양식은 제거한다."""
    try:
        root = Path(base_dir) / '.runtime/intelligence/unknown-articles'
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        return _blob(root, {'schema':1,'source_url':source_url,
                            'html':_safe_html(str(soup)),
                            'scripts_count':len(soup.select('script')),
                            'forms_count':len(soup.select('form')),
                            'status':'article_structure_unverified'})
    except Exception as exc:
        from together.operations.events import event
        event('검증','상세 양식 증거 보존 실패',console=False,error=type(exc).__name__,source_url=source_url)
        return None
