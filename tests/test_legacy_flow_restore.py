import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from together import application
from together.plato.star_cleanup import collect_old_stars, remove_star
from together.storage.email_outbox import EmailOutbox
from together.plato.service import WriteNoticeService
from together.plato import service as plato_service


class RestoredFlow(unittest.TestCase):
    def test_star_only_title_is_never_submitted_as_empty(self):
        service = MagicMock()
        service.driver.find_element.return_value.get_attribute.return_value = '⭐'
        with tempfile.TemporaryDirectory() as temp, patch('together.plato.star_cleanup.PlatoPostEditor'), patch('together.plato.star_cleanup.file_names', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'Empty title requires source review'):
                remove_star(service, 'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=123&bwid=456', Path(temp))
        service.driver.execute_script.assert_not_called()

    def test_legacy_navigation_recovers_after_more_than_three_failures(self):
        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = MagicMock()
        service.driver.get.side_effect = [TimeoutError()]*5+[None]
        service.restart_driver = MagicMock()
        with patch.object(plato_service.time, 'sleep'), patch.object(plato_service, 'event'):
            service.safe_get('https://dev-plato.pusan.ac.kr/')
        self.assertEqual(service.driver.get.call_count, 6)
        self.assertEqual(service.restart_driver.call_count, 5)

    def test_star_edit_preserves_body_and_attachment_snapshot(self):
        for old, options in [('⭐ old post', {}), ('⭐', {'recovered_subject':'old post', 'recovery_evidence':'original attachment filename'})]:
            with self.subTest(subject=old), tempfile.TemporaryDirectory() as temp:
                service = MagicMock()
                subject = MagicMock()
                subject.get_attribute.side_effect = [old, 'old post']
                body = MagicMock()
                body.get_attribute.return_value = '<p>Original content</p>'
                service.driver.find_element.side_effect = lambda by, name: body if name == 'id_contenteditable' else subject
                with patch('together.plato.star_cleanup.PlatoPostEditor') as editor, patch('together.plato.star_cleanup.file_names', return_value=['original.pdf']), patch('together.plato.star_cleanup.WebDriverWait'), patch('together.notices.intelligence.enrichment.PublishedNoticeStore') as store:
                    editor.return_value._notice_state.return_value = False
                    store.return_value.load.return_value = []
                    saved = remove_star(service, 'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=123&bwid=456', Path(temp), **options)
                proof = json.loads((Path(temp)/'.runtime/star-cleanup/456.json').read_text())
                self.assertEqual(saved, 'old post')
                self.assertTrue(proof['verified'])
                self.assertEqual(proof['before']['body_html'], proof['after']['body_html'])
                self.assertEqual(proof['after']['files'], ['original.pdf'])

    def test_one_star_edit_failure_keeps_other_posts_and_boards_running(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WriteNoticeService.__new__(WriteNoticeService)
            service.driver = MagicMock()
            service.driver.get_cookies.return_value = []
            service.driver.find_element.return_value.get_attribute.return_value = 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123'
            service.safe_get = MagicMock()
            service.restart_driver = MagicMock()
            service.login_ready = MagicMock()
            service.login = MagicMock()
            service.plato_id = 'self'
            service.plato_pw = 'test'
            with patch.object(plato_service, 'BASE_DIR', Path(temp)), patch('together.plato.star_cleanup.collect_old_stars', side_effect=[(['bad','good'], [1,2,3,4,5])] + [([], [1])]*8) as scan, patch('together.plato.star_cleanup.remove_star', side_effect=[RuntimeError(), 'preserved title']) as edit, patch.object(plato_service, 'event'):
                failures = service.remove_stars('https://dev-plato.pusan.ac.kr/course/view.php?id=157301', '2026-10-28')
            self.assertEqual(scan.call_count, 9)
            self.assertEqual(edit.call_count, 2)
            self.assertEqual(len(failures), 1)
            self.assertFalse(service._title_maintenance)
            self.assertEqual(service.login_ready.call_count, 2)
            self.assertEqual(service.login.call_count, 2)
            self.assertEqual(len(json.loads((Path(temp)/'.runtime/star-cleanup/pending.json').read_text())['items']), 1)

    def test_initialization_happens_before_browser_and_later_failure_keeps_committed_state(self):
        trace = []
        class LaterFailure(Exception):
            pass
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'yesterday.txt').write_text('new ordinary\nnew pinned\n')
            (root / 'pages.csv').write_text('committed university numbers')
            service = MagicMock()
            def prepare():
                trace.extend(['date', 'updater', 'initial-pages'])
                return ('2026-10-28', object(), object())
            def start():
                trace.append('browser')
                return service
            def cycle(*args):
                trace.extend(['participants', 'survey', 'university-update', 'reload-pages', 'other-department-failure'])
                raise LaterFailure()
            cwd = Path.cwd()
            try:
                with patch.object(application, 'BASE_DIR', root), patch.object(application, 'prepare_cycle', side_effect=prepare), patch.object(application, 'WriteNoticeService', side_effect=start), patch.object(application, 'run_cycle', side_effect=cycle), patch.object(application, 'finish_cycle'), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(LaterFailure):
                        application.main()
                self.assertEqual(trace, ['date','updater','initial-pages','browser','participants','survey','university-update','reload-pages','other-department-failure'])
                self.assertEqual((root / 'yesterday.txt').read_text(), 'new ordinary\nnew pinned\n')
                self.assertEqual((root / 'pages.csv').read_text(), 'committed university numbers')
                service.close.assert_called_once()
            finally:
                os.chdir(cwd)

    def test_star_scan_reaches_pages_five_six_and_seven_after_a_page_without_stars(self):
        class Response:
            def __init__(self, page):
                title = '⭐ old post' if page in (1, 5, 6, 7) else 'ordinary post'
                date = '2026-10-28' if page == 7 else '2026-10-27'
                self.text = f'<table><tbody><tr><td><a href="article.php?id=123&bwid={page}">{title}</a></td><td>{date}</td></tr></tbody></table><a href="view.php?id=123&page=7">last</a>'
            def raise_for_status(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        session = MagicMock()
        session.get.side_effect = lambda url, **kw: Response(kw['params']['page'])
        urls, pages = collect_old_stars(session, 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123', '2026-10-28')
        self.assertEqual(pages, [1,2,3,4,5,6,7])
        self.assertEqual([url.split('bwid=')[1] for url in urls], ['1','5','6'])

    def test_ordered_mail_does_not_skip_an_earlier_unconfirmed_or_delayed_request(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = EmailOutbox(temp)
            first = outbox.enqueue({'event_key':'first','department':'test','title':'first','recipients':['self']})
            outbox.enqueue({'event_key':'second','department':'test','title':'second','recipients':['self']})
            job = outbox.claim(ordered=True)
            self.assertEqual(job['id'], first['id'])
            self.assertIsNone(outbox.claim(ordered=True))
            outbox.defer(job, True, 'awaiting_email_receipt')
            self.assertIsNone(outbox.claim(ordered=True))
            outbox.complete(job, 1)
            self.assertEqual(outbox.claim(ordered=True)['payload']['title'], 'second')

    def test_recovered_boards_append_once_and_preserve_existing_csv_bytes(self):
        path = Path(__file__).resolve().parents[1] / 'ops/restore_boards.py'
        spec = importlib.util.spec_from_file_location('restore_boards_for_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'PAGE_NAME':'pages.csv'}):
            root = Path(temp)
            (root / 'together/crawler').mkdir(parents=True)
            (root / 'together/crawler/recovered_boards.json').write_text(json.dumps([{'department':'restored','previous_url':'https://old.invalid','page_url':'https://new.invalid','number':7}]))
            original = b'department,page_url,number\r\nexisting,https://existing.invalid,99\r\n'
            (root / 'pages.csv').write_bytes(original)
            self.assertEqual(module.restore_boards(root), ['restored'])
            once = (root / 'pages.csv').read_bytes()
            self.assertTrue(once.startswith(original))
            self.assertEqual(module.restore_boards(root), [])
            self.assertEqual((root / 'pages.csv').read_bytes(), once)
