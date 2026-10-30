import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from together.learning.budget import TrainingBudget, TrainingBudgetExceeded


class TrainingBudgetTests(unittest.TestCase):
    def test_unknown_request_keeps_reservation_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "budget.sqlite3"
            b = TrainingBudget(path, 1)
            self.assertTrue(b.reserve("unknown", .8))
            b = TrainingBudget(path, 1)
            self.assertFalse(b.reserve("unknown", .8))
            with self.assertRaises(TrainingBudgetExceeded): b.reserve("next", .3)
            b.settle("unknown", .1)
            self.assertTrue(b.reserve("next", .3))
            self.assertEqual(b.summary()["charged_usd"], .1)

    def test_parallel_reservation_cannot_overspend(self):
        with tempfile.TemporaryDirectory() as d:
            b = TrainingBudget(Path(d) / "budget.sqlite3", 1)
            def attempt(key):
                try: return b.reserve(key, .6)
                except TrainingBudgetExceeded: return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(attempt, ["one", "two"])), 1)

    def test_authorization_and_actual_cost_are_not_silently_changed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "budget.sqlite3"
            with self.assertRaises(ValueError): TrainingBudget(path, 96)
            b = TrainingBudget(path, 1)
            with self.assertRaises(ValueError): TrainingBudget(path, 2)
            b.reserve("one", .2)
            with self.assertRaises(TrainingBudgetExceeded): b.settle("one", .3)
            self.assertEqual(b.summary()["charged_usd"], .3)
            with self.assertRaises(TrainingBudgetExceeded): b.reserve("two", .1)
