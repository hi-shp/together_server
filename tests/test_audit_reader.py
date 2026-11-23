from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from together.operations.audit_reader import retained_audit_events, merge_retained_events

class AuditReaderTests(unittest.TestCase):
    def test_overlap_is_merged_without_erasing_repeated_real_sends(self):
        first = {'stage': 'Discord', 'time': '2026-11-23T15:00:00+09:00', 'message_id': '1'}
        other = dict(first, message_id='2')
        rows = merge_retained_events([first, first], [first, first, other])
        self.assertEqual(rows, [first, first, other])

    def test_read_is_bounded_and_does_not_mutate_the_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.runtime/audit-events.sqlite3'
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, at REAL, payload TEXT)')
                for i in range(3):db.execute('INSERT INTO events(at,payload) VALUES(?,?)', (i, json.dumps({'message_id': str(i)})))
                db.commit()
            before = path.read_bytes()
            self.assertEqual(retained_audit_events(root, 1, 1), [{'message_id': '1'}])
            self.assertEqual(before, path.read_bytes())
