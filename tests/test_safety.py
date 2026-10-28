"""외부 전송 없이 크롤러 동작을 검사한다."""

import contextlib
import csv
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules["together.integrations.openai_client"] = types.SimpleNamespace(
    answer_gpt=MagicMock(), check_title_similarity=MagicMock()
)

from together.plato import browser_runtime as browser_runtime
from together.crawler import announcements as crawl
import main
from together.crawler import pages as page_url_manager
import run_main
from together import config as runtime_config
from together.plato import service as selenium_service


class NetworkSafety(unittest.TestCase):
    def test_discord_confirmation_preserves_content_and_records_message_id(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"id": "123456789"}
        with (
            patch.dict(os.environ, {"TEST_WEBHOOK": "https://example.invalid"}),
            patch.object(
                runtime_config.requests, "post", return_value=response
            ) as post,
            patch.object(runtime_config, "_discord_result") as record,
        ):
            self.assertTrue(
                runtime_config.notify("기존 완료 문구", channel="TEST_WEBHOOK")
            )
        self.assertEqual(post.call_args.kwargs["json"], {"content": "기존 완료 문구"})
        self.assertEqual(post.call_args.kwargs["params"], {"wait": "true"})
        record.assert_called_once_with("TEST_WEBHOOK", "sent", 200, "123456789")
        response.close.assert_called_once()

    def test_missing_discord_does_not_send(self):
        with (
            patch.dict(os.environ, {"TEST_WEBHOOK": ""}),
            patch.object(runtime_config.requests, "post") as post,
        ):
            self.assertFalse(runtime_config.notify("notice", channel="TEST_WEBHOOK"))
            post.assert_not_called()

    def test_discord_failure_never_exposes_url_or_retries(self):
        output = io.StringIO()
        with (
            patch.dict(
                os.environ, {"TEST_WEBHOOK": "https://example.invalid/private-token"}
            ),
            patch.object(
                runtime_config.requests,
                "post",
                side_effect=runtime_config.requests.Timeout("private-token"),
            ) as post,
            contextlib.redirect_stdout(output),
        ):
            self.assertFalse(runtime_config.notify("notice", channel="TEST_WEBHOOK"))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 30))
        self.assertNotIn("private-token", output.getvalue())

    def test_discord_http_failure_is_nonfatal(self):
        response = MagicMock()
        response.raise_for_status.side_effect = runtime_config.requests.HTTPError()
        with (
            patch.dict(os.environ, {"TEST_WEBHOOK": "https://example.invalid"}),
            patch.object(runtime_config.requests, "post", return_value=response),
        ):
            self.assertFalse(runtime_config.notify("notice", channel="TEST_WEBHOOK"))


class CrawlerSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = Path.cwd()
        os.chdir(self.temp.name)
        Path("yesterday.txt").write_text("previous\nprevious\n", encoding="utf-8")

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def response(self, body, url="https://new.example/board/list"):
        response = MagicMock()
        response.text = body
        response.url = url
        return response

    def board(self, number=10):
        return page_url_manager.AnnouncementPage(
            "fixture", "https://old.example/list", "", number
        )

    def test_redirect_relative_links_and_oldest_first(self):
        html = """<table><tbody>
        <tr><td class="_artclTdNum">12</td><td class="_artclTdTitle"><a class="artclLinkView" href="../12">New</a></td></tr>
        <tr><td class="_artclTdNum">11</td><td class="_artclTdTitle"><a class="artclLinkView" href="../11">Old</a></td></tr>
        </tbody></table>"""
        with patch.object(crawl, "http_get", return_value=self.response(html)):
            urls, number = crawl.get_anns_url(self.board(), 1)
        self.assertEqual(urls, ["https://new.example/11", "https://new.example/12"])
        self.assertEqual(number, 12)

    def test_partial_layout_failure_preserves_watermark(self):
        html = """<tbody><tr><td class="td-num">12</td><td class="td-title">missing</td></tr>
        <tr><td class="td-num">11</td><td class="td-title"><a href="/11">Old</a></td></tr></tbody>"""
        with patch.object(crawl, "http_get", return_value=self.response(html)):
            self.assertEqual(crawl.get_anns_url(self.board(), 1), ([], 10))

    def test_http_failure_preserves_watermark(self):
        with patch.object(crawl, "http_get", side_effect=crawl.requests.Timeout):
            self.assertEqual(crawl.get_anns_url(self.board(), 1), ([], 10))

    def test_disabled_board_is_fetched_once(self):
        with patch.object(
            crawl, "http_get", return_value=self.response("<h1>disabled</h1>")
        ) as get:
            self.assertEqual(crawl.get_anns_url(self.board(), 1), ([], 10))
            self.assertEqual(get.call_count, 1)

    def test_relative_image_uses_final_document_url(self):
        response = self.response(
            '<h2 class="artclViewTitle">Title</h2><div class="artclView"><img src="../image.png">Body</div>',
            "https://new.example/board/post/123",
        )
        with patch.object(crawl, "http_get", return_value=response):
            ann = crawl.crawl_ann("https://old.example/123", "해당없음")
        self.assertIn("https://new.example/board/image.png", ann.content_html)

    def test_csv_update_preserves_other_rows_and_schema(self):
        Path("pages.csv").write_text(
            "department,page_url,number\nA,https://a,10\nB,https://b,20\n",
            encoding="utf-8",
        )
        main.update_csv_with_announcement_numbers({"https://a": 11}, "pages.csv")
        with Path("pages.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            rows,
            [
                {"department": "A", "page_url": "https://a", "number": "11"},
                {"department": "B", "page_url": "https://b", "number": "20"},
            ],
        )

    def test_url_migration_preserves_csv_key_and_number(self):
        source = "https://koredu.pusan.ac.kr/koredu/26185/subview.do"
        page = page_url_manager.AnnouncementPage("국어교육과", source, "", 943)
        self.assertEqual(page.source_url, source)
        self.assertEqual(
            page.page_url, "https://koredu.pusan.ac.kr/koredu/16597/subview.do"
        )
        self.assertEqual(page.number, 943)

    def test_missing_partial_never_posts_or_advances(self):
        service = MagicMock()
        manager = types.SimpleNamespace(announcement_pages=[self.board()])
        with (
            patch.object(main, "NoticeUpdater"),
            patch.object(main, "PageUrlManager", return_value=manager),
            patch.object(
                main, "get_anns_url", return_value=(["https://example.invalid/11"], 11)
            ),
            patch.object(main, "crawl_ann_partial", return_value=None),
            patch.object(main.time, "sleep"),
            patch.object(main, "notify"),
            patch.object(main, "update_csv_with_announcement_numbers") as update,
            patch.object(main.os.path, "expanduser", return_value=self.temp.name),
        ):
            with self.assertRaises(RuntimeError):
                main.run_cycle(service)
        service.write_notices.assert_not_called()
        service.send_message.assert_not_called()
        update.assert_not_called()


class BrowserSafety(unittest.TestCase):
    def test_constructor_failure_cleans_up(self):
        with (
            patch.object(
                selenium_service.WriteNoticeService,
                "init_driver",
                side_effect=RuntimeError,
            ),
            patch.object(selenium_service.WriteNoticeService, "close") as close,
        ):
            with self.assertRaises(RuntimeError):
                selenium_service.WriteNoticeService()
        close.assert_called_once()

    def test_quit_failure_still_stops_service_and_cleans_workspace(self):
        service = selenium_service.WriteNoticeService.__new__(
            selenium_service.WriteNoticeService
        )
        service.driver = MagicMock()
        service.driver.quit.side_effect = RuntimeError()
        service._service = MagicMock()
        service._workspace = Path("/fake/driver-123")
        with (
            patch.object(selenium_service, "driver_children", return_value=[]),
            patch.object(selenium_service, "stop_children"),
            patch.object(selenium_service, "cleanup_workspace") as cleanup,
        ):
            service.close()
        service._service.stop.assert_called_once()
        cleanup.assert_called_once_with(Path("/fake/driver-123"))
        self.assertIsNone(service.driver)

    def test_cleanup_rejects_foreign_directory(self):
        with self.assertRaises(ValueError):
            browser_runtime.cleanup_workspace("/tmp")

    def test_main_exception_always_closes_browser(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(main, "BASE_DIR", Path(temp)),
            patch.object(main, "prepare_cycle", return_value=("2026-10-28", None, None)),
            patch.object(main, "WriteNoticeService") as service,
            patch.object(main, "run_cycle", side_effect=RuntimeError),
        ):
            old = Path.cwd()
            try:
                with self.assertRaises(RuntimeError):
                    main.main()
            finally:
                os.chdir(old)
        service.return_value.close.assert_called_once()


class SupervisorSafety(unittest.TestCase):
    def run_fixture(self, script, **options):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text(script, encoding="utf-8")
            (root / ".runtime").mkdir()
            with (
                patch.object(run_main, "BASE_DIR", root),
                patch.object(run_main, "scratch_root", return_value=root / ".runtime"),
                patch.object(
                    run_main.sys, "stdout", types.SimpleNamespace(buffer=io.BytesIO())
                ),
            ):
                result = run_main.run_cycle(root / "log.txt", **options)
            self.assertEqual(list((root / ".runtime").glob("run-*")), [])
            return result

    def test_progress_keeps_a_long_cycle_running(self):
        self.assertEqual(
            self.run_fixture(
                "import time\nfor i in range(6):\n print(i,flush=True)\n time.sleep(.2)\n",
                idle_seconds=0.8,
            ),
            0,
        )

    def test_no_progress_still_stops_a_stuck_cycle(self):
        self.assertEqual(
            self.run_fixture(
                "import time\nprint('started',flush=True)\ntime.sleep(60)\n",
                idle_seconds=0.2,
            ),
            124,
        )

    def test_retention_does_not_touch_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "log_2020-01-01_00-00-00.txt"
            old.write_text("old")
            os.utime(old, (1, 1))
            foreign = root / "important.txt"
            foreign.write_text("keep")
            run_main.cleanup_old_logs(root)
            self.assertFalse(old.exists())
            self.assertTrue(foreign.exists())

    def test_explicit_test_timeout_keeps_the_full_log_and_cleans_own_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text(
                "import subprocess,sys,time\n"
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                "print('child-pid='+str(p.pid),flush=True)\n"
                "print('x'*200000,flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            try:
                (root / ".runtime").mkdir()
                with (
                    patch.object(run_main, "BASE_DIR", root),
                    patch.object(
                        run_main, "scratch_root", return_value=root / ".runtime"
                    ),
                    patch.object(
                        run_main.sys,
                        "stdout",
                        types.SimpleNamespace(buffer=io.BytesIO()),
                    ),
                ):
                    self.assertEqual(
                        run_main.run_cycle(root / "log.txt", max_seconds=2), 124
                    )
                self.assertGreater((root / "log.txt").stat().st_size, 200000)
                self.assertEqual(list((root / ".runtime").glob("run-*")), [])
                self.assertIsNone(unrelated.poll())
                child_pid = int(
                    (root / "log.txt").read_text().splitlines()[0].split("=")[1]
                )
                try:
                    state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
                    self.assertEqual(state, "Z")  # 종료된 자식은 init이 회수한다.
                except FileNotFoundError:
                    pass
            finally:
                unrelated.terminate()
                unrelated.wait()


if __name__ == "__main__":
    unittest.main()
