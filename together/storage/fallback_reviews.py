"""로컬 대체 판단과 복구 후 실제 API 검토를 삭제 없이 연결한다."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


class FallbackReviews:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / '.runtime/fallback-reviews.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        os.chmod(self.path, 0o600)
        db.execute('''CREATE TABLE IF NOT EXISTS reviews
            (key TEXT PRIMARY KEY,model TEXT,request TEXT,local_result TEXT,context TEXT,
             error TEXT,state TEXT,created REAL,updated REAL,attempts INTEGER DEFAULT 0,
             reviewed_result TEXT,action TEXT)''')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, model, request, result, context, error):
        raw = json.dumps([model, request, context.get('source_url'), context.get('department')], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(raw.encode()).hexdigest()
        with self.connection() as db:
            # 같은 원문의 대체 판단과 재검증 이력을 뒤의 재시도로 덮지 않는다.
            db.execute('INSERT OR IGNORE INTO reviews (key,model,request,local_result,context,error,state,created,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                       (key, model, json.dumps(request, ensure_ascii=False), json.dumps(result, ensure_ascii=False), json.dumps(context, ensure_ascii=False), type(error).__name__, 'pending', time.time(), time.time()))
        return key

    def pending(self):
        with self.connection() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM reviews WHERE state IN ('pending','correction_pending') ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def update(self, key, state, result=None, action=None):
        with self.connection() as db:
            db.execute('UPDATE reviews SET state=?,reviewed_result=COALESCE(?,reviewed_result),action=COALESCE(?,action),updated=?,attempts=attempts+1 WHERE key=?',
                       (state, json.dumps(result, ensure_ascii=False) if result is not None else None, json.dumps(action, ensure_ascii=False) if action is not None else None, time.time(), key))

    def summary(self):
        with self.connection() as db:
            return dict(db.execute('SELECT state,COUNT(*) FROM reviews GROUP BY state'))
