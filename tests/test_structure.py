"""이전 모듈 경로와 서버 데이터 경로를 유지하는지 확인한다."""
import importlib
from pathlib import Path
import unittest


class StructureCompatibilityTests(unittest.TestCase):
    def test_previous_import_paths_resolve_to_the_same_implementation(self):
        modules={'main':'together.application','run_main':'together.supervisor',
            'selenium_service':'together.plato.service','notification_transport':'together.plato.notifications',
            'notification_content':'together.plato.content','runtime_config':'together.config',
            'crawl_announcement':'together.crawler.announcements','attachment_cache':'together.storage.attachments'}
        for old,new in modules.items():
            with self.subTest(module=old):self.assertIs(importlib.import_module(old),importlib.import_module(new))

    def test_configuration_and_entry_points_stay_at_the_repository_root(self):
        from together.config import BASE_DIR
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(BASE_DIR,root)
        for name in ('main.py','run_main.py','env.json'):
            self.assertTrue((BASE_DIR/name).exists(),name)
