"""Explicitly retired subscription identities; no subscriber data is written in plaintext."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

class RecipientExclusions:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / '.runtime/recipient-exclusions.sqlite3'

    @staticmethod
    def key(kind, person):
        identity = person.get('student_id', '') if kind == 'message' else person.get('email', '')
        return hashlib.sha256(json.dumps([kind, person['name'].strip(), identity.strip()], ensure_ascii=False).encode()).hexdigest()

    def filter(self, kind, people):
        if not self.path.exists():
            return people
        with sqlite3.connect('file:' + str(self.path) + '?mode=ro', uri=True) as db:
            retired = {row[0] for row in db.execute('SELECT identity FROM exclusions WHERE kind=?', (kind,))}
        return [person for person in people if self.key(kind, person) not in retired]

    def remember_absent(self, kind, people, targets, student_ids):
        from together.plato.notifications import normalize, resolve_recipients
        if not targets:
            return 0
        absent = []
        for person in people:
            if resolve_recipients(targets, [person], kind, student_ids)[0]:
                continue
            identity = person.get('student_id', '') if kind == 'message' else person.get('email', '')
            token = normalize('(' + identity + ')')
            if not identity or any(token in t['title'] or token in t['text'] for t in targets):
                continue  # Ambiguous accounts or changed names are not absent people.
            if kind == 'email' and (not person.get('student_id') or person['student_id'] in student_ids.values()):
                continue  # A current student's changed email address needs reconciliation.
            absent.append(self.key(kind, person))
        if not absent:
            return 0
        self.path.parent.mkdir(exist_ok=True, parents=True)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS exclusions (identity TEXT PRIMARY KEY, kind TEXT, at REAL, reason TEXT)')
            db.executemany('INSERT OR IGNORE INTO exclusions VALUES (?,?,?,?)', [(key, kind, time.time(), 'absent_from_school_directory') for key in absent])
        os.chmod(self.path, 0o600)
        return len(absent)
