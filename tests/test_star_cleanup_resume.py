import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from together.plato import service as module


class StarCleanupResume(unittest.TestCase):
    def test_interruption_retains_current_and_unvisited_boards(self):
        for finished in (0, 1):
            with self.subTest(finished=finished), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service = module.WriteNoticeService.__new__(module.WriteNoticeService)
                service._http = MagicMock()
                service._http.board_url.side_effect = lambda name: name
                pending = root / '.runtime/star-cleanup/pending.json'

                def interrupted(*args):
                    # The durable marker exists before any network operation.
                    self.assertEqual(len(json.loads(pending.read_text())['items']), 9)
                    raise SystemExit(143)

                effects = iter([([], [1])] * finished)

                def scan(*args):
                    try:
                        return next(effects)
                    except StopIteration:
                        return interrupted(*args)

                with patch.object(module, 'BASE_DIR', root), patch.object(module, 'event'), patch('together.plato.star_cleanup.collect_old_stars', side_effect=scan):
                    with self.assertRaises(SystemExit):
                        service.remove_stars('course', '2026-11-01')
                items = json.loads(pending.read_text())['items']
                self.assertEqual(len(items), 9 - finished)
                self.assertTrue(all(item['error'] == 'cleanup_interrupted' for item in items))
                self.assertFalse(service._title_maintenance)
                self.assertFalse(pending.with_suffix('.pending').exists())

    def test_full_cleanup_clears_pending_only_after_all_boards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = module.WriteNoticeService.__new__(module.WriteNoticeService)
            service._http = MagicMock()
            with patch.object(module, 'BASE_DIR', root), patch.object(module, 'event'), patch('together.plato.star_cleanup.collect_old_stars', return_value=([], [1])) as scan:
                self.assertEqual(service.remove_stars('course', '2026-11-01'), [])
            self.assertEqual(scan.call_count, 9)
            self.assertEqual(json.loads((root / '.runtime/star-cleanup/pending.json').read_text())['items'], [])
