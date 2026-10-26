"""게시 동작을 바꾸지 않고 분류·대표 원문 후보를 별도로 기록한다."""
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .events import Notice, group_candidates, select_representative
from .routing import configured_classifier


class ShadowRecorder:
    def __init__(self, base_dir):
        self.root = Path(base_dir) / '.runtime/intelligence'
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.connection = sqlite3.connect(self.root / 'observations.sqlite3', timeout=.2)
        os.chmod(self.root / 'observations.sqlite3', 0o600)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA max_page_count=32768')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS notices
            (id INTEGER PRIMARY KEY, session_id TEXT, observed_at TEXT, source_url TEXT, department TEXT,
             title TEXT, body_excerpt TEXT, body_sha256 TEXT, metadata_json TEXT, local_prediction TEXT)''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS categories
            (id INTEGER PRIMARY KEY, session_id TEXT, observed_at TEXT, title TEXT,
             actual_category TEXT, actual_method TEXT, local_prediction TEXT)''')
        self.session_id = uuid.uuid4().hex
        self.notices = []
        self.classifier = configured_classifier(base_dir)

    def notice(self, announcement, department):
        title = getattr(announcement, 'original_title', announcement.title)
        body = announcement.content_text or ''
        metadata = getattr(announcement, 'metadata', {}) or {}
        observed = datetime.now().isoformat(timespec='seconds')
        prediction = self.classifier.predict(title) if self.classifier else None
        self.connection.execute('''INSERT INTO notices
            (session_id,observed_at,source_url,department,title,body_excerpt,body_sha256,metadata_json,local_prediction)
            VALUES (?,?,?,?,?,?,?,?,?)''', (
            self.session_id, observed, announcement.url, department, title, body[:6000],
            hashlib.sha256(body.encode('utf-8')).hexdigest(), json.dumps(metadata, ensure_ascii=False),
            json.dumps(prediction, ensure_ascii=False)))
        self.connection.execute('DELETE FROM notices WHERE id <= (SELECT COALESCE(MAX(id),0)-10000 FROM notices)')
        self.connection.commit()
        if len(self.notices) < 200:
            self.notices.append(Notice(title=title, observed_at=observed, source_url=announcement.url,
                                       department=department, body_text=body[:6000],
                                       attachment_urls=[item['url'] for item in metadata.get('attachment_links', [])],
                                       metadata_complete=bool(metadata.get('attachment_scan_complete')),
                                       publication_time=metadata.get('publication_time') or ''))

    def category(self, title, category, method):
        prediction = self.classifier.predict(title) if self.classifier else None
        self.connection.execute('INSERT INTO categories VALUES (NULL,?,?,?,?,?,?)', (
            self.session_id, datetime.now().isoformat(timespec='seconds'), title, category, method,
            json.dumps(prediction, ensure_ascii=False)))
        self.connection.execute('DELETE FROM categories WHERE id <= (SELECT COALESCE(MAX(id),0)-10000 FROM categories)')
        self.connection.commit()

    def finish(self):
        proposals = []
        for group in group_candidates(self.notices):
            result = select_representative(group)
            proposals.append({'representative_source': result['winner'].source_url,
                              'representative_title': result['quality']['display_title'],
                              'source_count': len(group), 'quality': result['quality'],
                              'all_departments': result['departments'], 'all_source_urls': result['source_urls'],
                              'historical_quality_verifiable': result['historical_quality_verifiable']})
        target = self.root / 'last-cycle-proposal.json'
        temporary = self.root / f'.proposal-{self.session_id}.tmp'
        temporary.write_text(json.dumps({'proposal_only': True, 'posts_changed': False,
                                         'existing_posts_edited': False, 'session_id': self.session_id,
                                         'observed_candidates': len(self.notices), 'groups': proposals},
                                        ensure_ascii=False, indent=2), encoding='utf-8')
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)


@lru_cache(maxsize=1)
def _recorder(base_dir):
    return ShadowRecorder(base_dir)


def _record(method, *args):
    if os.getenv('NOTICE_INTELLIGENCE_MODE', 'off') != 'shadow':
        return
    try:
        from together.config import BASE_DIR
        getattr(_recorder(str(BASE_DIR)), method)(*args)
    except Exception as exc:
        print(f'별도 공지 분석 기록 생략: {type(exc).__name__}', flush=True)


def observe_notice(announcement, department):
    _record('notice', announcement, department)


def observe_category(title, category, method):
    _record('category', title, category, method)


def finish_cycle():
    _record('finish')
