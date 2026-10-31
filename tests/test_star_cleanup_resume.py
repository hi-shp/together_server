import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from together.plato import service as module
from together.plato.star_cleanup import remove_star, unverified_star_edits


class StarCleanupResume(unittest.TestCase):
    def test_saved_edit_is_reconciled_without_resubmit_and_changes_are_not_overwritten(self):
        url = 'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=123&bwid=456'
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                proof = root / '.runtime/star-cleanup/456.json'
                proof.parent.mkdir(parents=True)
                before = {'subject': '⭐ old post', 'body_html': '<p>Original</p>', 'files': ['original.pdf'], 'notice': False}
                proof.write_text(json.dumps({'article_url': url, 'before': before}), encoding='utf-8')
                self.assertEqual(unverified_star_edits(root, 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123'), [url])
                self.assertEqual(unverified_star_edits(root, 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=999'), [])
                service = MagicMock()
                title, body = MagicMock(), MagicMock()
                title.get_attribute.return_value = 'old post'
                body.get_attribute.return_value = '<p>Changed by someone</p>' if changed else before['body_html']
                service.driver.find_element.side_effect = lambda by, name: body if name == 'id_contenteditable' else title
                with patch('together.plato.star_cleanup.PlatoPostEditor') as editor, patch('together.plato.star_cleanup.file_names', return_value=['original.pdf']), patch('together.notices.intelligence.enrichment.PublishedNoticeStore') as store:
                    editor.return_value._notice_state.return_value = False
                    store.return_value.load.return_value = []
                    if changed:
                        with self.assertRaisesRegex(RuntimeError, 'differs from backup'):
                            remove_star(service, url, root)
                    else:
                        self.assertEqual(remove_star(service, url, root), 'old post')
                service.driver.execute_script.assert_not_called()
                title.click.assert_not_called()
                saved = json.loads(proof.read_text(encoding='utf-8'))
                self.assertEqual(saved['before'], before)
                self.assertEqual(bool(saved.get('verified')), not changed)
                if not changed:
                    self.assertTrue(saved['reconciled_without_resubmit'])
                    self.assertEqual(unverified_star_edits(root, 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123'), [])

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
