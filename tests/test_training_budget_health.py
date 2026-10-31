import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from together.storage.api_usage import ApiAccounting
from tools.health_check import budget_status


class TrainingBudgetHealth(unittest.TestCase):
    def prepare(self, root, ledger):
        path = root / '.runtime/health/api-budget.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            'key_fingerprint': hashlib.sha256(os.getenv('GPT_API_KEY', '').encode()).hexdigest(),
            'observed_at': '2026-10-30T00:00:00+09:00',
            'credit_expires_at': '2026-11-30T00:00:00+09:00',
            'balance_usd': 10, 'training_ledger_paths': [str(ledger), str(ledger)],
        }))
        from datetime import datetime
        at = datetime.fromisoformat('2026-10-30T00:00:00+09:00').timestamp()
        with ApiAccounting(root).connection() as db:
            db.execute('INSERT INTO usage(at,cost) VALUES (?,?)', (at+1, .5))
        return at

    def test_separate_training_cost_and_unknown_reservation_are_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / 'training.sqlite3'
            at = self.prepare(root, ledger)
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE requests (charged INTEGER,reserved INTEGER,at REAL)')
                db.executemany('INSERT INTO requests VALUES (?,?,?)', [
                    (3000000, 4000000, at+1), (None, 1000000, at+2),
                    (2000000, 2000000, at-1),
                ])
            result = budget_status(root, {})
            self.assertEqual(result['estimated_remaining_usd'], 5.5)
            self.assertEqual(result['tracked_training_charged_usd'], 3)
            self.assertEqual(result['unconfirmed_training_reserved_usd'], 1)
            self.assertEqual(result['training_ledgers_included'], 1)

    def test_missing_training_ledger_does_not_report_an_overstated_balance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / 'missing.sqlite3'
            self.prepare(root, ledger)
            result = budget_status(root, {})
            self.assertEqual(result['balance_source'], 'training_ledger_unavailable')
            self.assertIsNone(result['estimated_remaining_usd'])
            self.assertFalse(ledger.exists())
