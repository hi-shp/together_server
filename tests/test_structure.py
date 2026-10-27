"""실행 경로, 저장 위치와 분류 기준을 유지하는지 확인한다."""

import hashlib
import importlib
import json
from pathlib import Path
import unittest


class StructureCompatibilityTests(unittest.TestCase):
    def test_entry_points_use_the_same_implementation(self):
        for old, new in {
            "main": "together.application",
            "run_main": "together.supervisor",
        }.items():
            self.assertIs(importlib.import_module(old), importlib.import_module(new))

    def test_configuration_and_entry_points_stay_at_the_repository_root(self):
        from together.config import BASE_DIR

        root = Path(__file__).resolve().parents[1]
        self.assertEqual(BASE_DIR, root)
        self.assertEqual(
            {path.name for path in BASE_DIR.glob("*.py")}, {"main.py", "run_main.py"}
        )
        self.assertFalse((BASE_DIR / "env.json").exists())

    def test_category_keyword_and_exclusion_values_and_order_are_unchanged(self):
        from together.notices.intelligence.policy import policy_config

        actual = json.dumps(
            policy_config(), ensure_ascii=False, separators=(",", ":")
        ).encode()
        self.assertEqual(
            hashlib.sha256(actual).hexdigest(),
            "e6a2f6876695653dd823677fc0ef93abf78d87ea76d4d38dc2999c06355f85fc",
        )
