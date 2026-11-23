"""Keep compact verification receipts independently of high-volume progress logs."""
from datetime import datetime
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time


STAGES = frozenset(("Discord", "중복", "분류", "게시", "쪽지", "이메일", "보강", "첨부", "정리"))
RETENTION = 180 * 86400


def retain_event(base_dir, record, *, now=None):
    """Only accept the already sanitized event; never save webhook or recipient data."""
    if record.get("stage") not in STAGES:
        return
    captured = time.time() if now is None else now
    root = Path(base_dir) / ".runtime"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "audit-events.sqlite3"
    try:
        stamp = datetime.fromisoformat(record["time"]).timestamp()
    except (KeyError, TypeError, ValueError):
        stamp = captured
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        os.chmod(path, 0o600)
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL NOT NULL, captured REAL NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS audit_events_at ON events(at)")
        db.execute("INSERT INTO events(at,captured,payload) VALUES (?,?,?)", (stamp, captured, json.dumps(record, ensure_ascii=False)))
        db.execute("DELETE FROM events WHERE at<?", (captured - RETENTION,))
