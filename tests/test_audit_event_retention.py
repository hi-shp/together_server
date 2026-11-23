import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from together.storage.audit_events import retain_event


class AuditEventRetentionTests(unittest.TestCase):
    def test_receipts_survive_progress_log_rotation_and_keep_distinct_sends(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = {'stage': 'Discord', 'time': '2026-11-23T15:00:00+09:00', 'message_id': '12345', 'filename': 'log_a.txt', 'sha256': 'a' * 64}
            now = datetime.fromisoformat(event['time']).timestamp()
            retain_event(root, event, now=now)
            retain_event(root, dict(event, message_id='67890'), now=now)
            telemetry = root / '.runtime/telemetry'
            telemetry.mkdir()
            (telemetry / 'events.jsonl').write_text('rotated progress')
            (telemetry / 'events.jsonl').unlink()
            with sqlite3.connect(root / '.runtime/audit-events.sqlite3') as db:
                rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM events ORDER BY id')]
            self.assertEqual([r['message_id'] for r in rows], ['12345', '67890'])
            self.assertEqual(rows[0]['sha256'], 'a' * 64)

    def test_no_change_progress_does_not_fill_the_receipt_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            retain_event(temp, {'stage': '수집', 'message': '새 공지 없음'})
            self.assertFalse((Path(temp) / '.runtime/audit-events.sqlite3').exists())

    def test_expired_receipts_are_pruned_and_current_ones_remain(self):
        with tempfile.TemporaryDirectory() as temp:
            now = datetime(2026, 11, 23, tzinfo=timezone.utc).timestamp()
            retain_event(temp, {'stage': 'Discord', 'time': '2025-01-01T00:00:00+00:00'}, now=now)
            retain_event(temp, {'stage': 'Discord', 'time': '2026-11-23T00:00:00+00:00'}, now=now)
            with sqlite3.connect(Path(temp) / '.runtime/audit-events.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_full_log_receipt_records_uploaded_filename_and_hash(self):
        from together import config
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'log_sample.txt'
            path.write_bytes(b'full cycle log\n')
            with patch('together.operations.events.event') as event:
                config._discord_result('DISCORD_LOG_WEBHOOK_URL', 'sent', 200, '12345', log_path=path)
            fields = event.call_args.kwargs
            self.assertEqual(fields['filename'], path.name)
            self.assertEqual(fields['bytes'], path.stat().st_size)
            self.assertEqual(len(fields['sha256']), 64)

