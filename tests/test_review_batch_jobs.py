import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from together.learning.budget import TrainingBudget

spec = importlib.util.spec_from_file_location('review_batch_jobs', Path(__file__).resolve().parents[1] / 'tools/review_batch_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)


class BatchAcceptanceTests(unittest.TestCase):
    def test_unknown_acceptance_is_reconciled_without_another_paid_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'input.jsonl').write_text('{}\n')
            digest = hashlib.sha256((root / 'input.jsonl').read_bytes()).hexdigest()
            plan = {'file':'input.jsonl','sha256':digest,'model':'test','reservations':[{'key':'request','maximum_usd':1}]}
            budget = TrainingBudget(root / 'budget.sqlite3')
            client = MagicMock()
            client.files.create.return_value.id = 'input'
            client.batches.create.side_effect = TimeoutError('connection interrupted')
            with self.assertRaises(TimeoutError): jobs.submit(client,root,plan,budget)
            self.assertEqual(budget.summary()['unconfirmed_reserved_usd'],1)
            client.batches.list.return_value = []
            with self.assertRaises(RuntimeError): jobs.submit(client,root,plan,budget)
            client.batches.create.assert_called_once()
            client.batches.list.return_value = [SimpleNamespace(id='accepted',metadata={'together_input_sha':digest})]
            self.assertEqual(jobs.submit(client,root,plan,budget)['batch_id'],'accepted')
            self.assertEqual(jobs.submit(client,root,plan,budget)['batch_id'],'accepted')
            client.batches.create.assert_called_once()
            client.files.create.assert_called_once()

    def test_altered_input_cannot_reuse_cost_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'input.jsonl').write_text('changed')
            client=MagicMock()
            with self.assertRaises(ValueError):
                jobs.submit(client,root,{'file':'input.jsonl','sha256':'old'},MagicMock())
            client.batches.create.assert_not_called()
