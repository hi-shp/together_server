import csv, json, os, tempfile, types, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from together.crawler.university import NoticeUpdater
from together.crawler.university_state import (
    UniversityCheckpoint,
    known_title,
    pinned_candidate,
)
from together.crawler import announcements as crawl
from together.notices.intelligence.post_editor import PlatoPostEditor


class Response:
    url = "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1&mgr_seq=3"

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class UniversityRecovery(unittest.TestCase):
    def setup_files(self, root):
        (root / "yesterday.txt").write_text(
            "이전 일반 제목\n기존 고정 제목\n", encoding="utf-8"
        )
        (root / "titles.txt").write_text(
            "2026-10-26 10:00:00$기존 고정 제목\n2026-10-26 10:00:00$번호에서 고정으로 이동\n2026-10-26 10:00:00$이전 일반 제목\n",
            encoding="utf-8",
        )
        (root / "pages.csv").write_text(
            "department,page_url,number\n대학,https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1,9\n대학,https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=2,9\n기계,https://example.invalid/list,3\n",
            encoding="utf-8",
        )

    def test_pinned_items_keep_the_original_first_new_item_boundary(self):
        rows = "".join(
            f'<tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?mode=view&board_seq={i}">{title}</a></td><td>2026-10-27</td></tr>'
            for i, title in enumerate(
                [
                    "새 고정 하나",
                    "기존 고정 제목",
                    "번호에서 고정으로 이동",
                    "새 고정 둘",
                ],
                1,
            )
        )
        rows += '<tr><td class="num">9</td><td class="subject"><a href="?mode=view&board_seq=9">이전 일반 제목</a></td><td>2026-10-27</td></tr>'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_files(root)
            old = Path.cwd()
            os.chdir(root)
            page = types.SimpleNamespace(
                page_url=Response.url, department="대학", number=9
            )
            try:
                with (
                    patch.object(crawl, "BASE_DIR", root),
                    patch.object(
                        crawl,
                        "http_get",
                        return_value=Response(
                            "<table><tbody>" + rows + "</tbody></table>"
                        ),
                    ),
                ):
                    urls, number = crawl.get_anns_url(page, 1)
                self.assertEqual(number, 9)
                self.assertEqual(len(urls), 1)
                self.assertTrue(any("board_seq=1" in u for u in urls))
                self.assertFalse(any("board_seq=4" in u for u in urls))
                self.assertTrue(
                    known_title(root, "번호에서 고정으로 이동", "2026-10-27")
                )
            finally:
                os.chdir(old)

    def test_annual_same_title_is_compared_with_its_publication_date(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "titles.txt").write_text(
                "2025-10-27 10:00:00$겨울 행사 모집\n", encoding="utf-8"
            )
            self.assertTrue(known_title(root, "겨울 행사 모집", "2025-10-25"))
            self.assertFalse(known_title(root, "겨울 행사 모집", "2026-10-27"))

    def test_unknown_title_below_saved_number_is_not_recovered(self):
        body = '<table><tbody><tr><td class="num">9</td><td class="subject"><a href="?mode=view&board_seq=10">오늘 새 제목</a></td><td>2026-10-27</td></tr></tbody></table>'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_files(root)
            old = Path.cwd()
            os.chdir(root)
            page = types.SimpleNamespace(
                page_url=Response.url, department="대학", number=100
            )
            try:
                with (
                    patch.object(crawl, "BASE_DIR", root),
                    patch.object(crawl, "http_get", return_value=Response(body)),
                ):
                    urls, number = crawl.get_anns_url(page, 1)
                self.assertEqual(urls, [])
            finally:
                os.chdir(old)

    def test_original_title_rebasing_handles_changed_display_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            updater = NoticeUpdater(
                str(Path(temp) / "yesterday.txt"), str(Path(temp) / "pages.csv")
            )
            body = '<table><tr><td class="num">27</td><td class="subject"><a>이전 일반 제목</a></td></tr></table>'
            with patch(
                "together.crawler.university.requests.get", return_value=Response(body)
            ):
                self.assertEqual(updater.fetch_notice_number("이전 일반 제목"), "27")
            body = '<table><tr><td class="num"><img alt="공지"></td><td class="subject"><a>이전 일반 제목</a></td></tr></table>'
            with patch(
                "together.crawler.university.requests.get", return_value=Response(body)
            ):
                self.assertIsNone(updater.fetch_notice_number("이전 일반 제목"))

    def test_failure_restores_only_university_numbers_and_both_title_markers(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"PAGE_NAME": "pages.csv"}),
        ):
            root = Path(temp)
            self.setup_files(root)
            with self.assertRaises(RuntimeError):
                with UniversityCheckpoint(root):
                    (root / "yesterday.txt").write_text(
                        "앞서 저장된 제목\n새 고정\n", encoding="utf-8"
                    )
                    text = (
                        (root / "pages.csv")
                        .read_text()
                        .replace(",9\n", ",30\n")
                        .replace(",3\n", ",7\n")
                    )
                    (root / "pages.csv").write_text(text)
                    raise RuntimeError("interrupted")
            self.assertEqual(
                (root / "yesterday.txt").read_text(), "이전 일반 제목\n기존 고정 제목\n"
            )
            with (root / "pages.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["number"] for r in rows], ["9", "9", "7"])

    def test_restarts_restore_an_interrupted_checkpoint_and_success_keeps_new_titles(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"PAGE_NAME": "pages.csv"}),
        ):
            root = Path(temp)
            self.setup_files(root)
            UniversityCheckpoint(root).__enter__()
            (root / "yesterday.txt").write_text(
                "아직 처리 전\n고정\n", encoding="utf-8"
            )
            with UniversityCheckpoint(root):
                self.assertTrue(
                    (root / "yesterday.txt").read_text().startswith("이전 일반 제목")
                )
                (root / "yesterday.txt").write_text(
                    "처리 완료 제목\n고정 완료\n", encoding="utf-8"
                )
            self.assertTrue(
                (root / "yesterday.txt").read_text().startswith("처리 완료 제목")
            )

    def test_created_post_is_reused_until_notification_registration_completes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_files(root)
            (root / ".runtime").mkdir()
            source = "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=2&board_seq=123"
            path = root / ".runtime/published-receipts.json"
            path.write_text(
                json.dumps({"key": {"source_url": source, "state": "created"}})
            )
            moved = "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1&board_seq=123"
            self.assertEqual(pinned_candidate(root, moved, "기존 고정 제목"), source)
            path.write_text(
                json.dumps(
                    {"key": {"source_url": source, "notifications_registered": True}}
                )
            )
            self.assertIsNone(pinned_candidate(root, moved, "기존 고정 제목"))


class OptionalBoardFields(unittest.TestCase):
    def test_reading_boards_with_and_without_a_notice_checkbox(self):
        url = "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1885169&bwid=2474380"
        for available in (False, True):
            with (
                self.subTest(available=available),
                tempfile.TemporaryDirectory() as temp,
            ):
                driver = MagicMock()
                driver.find_element.return_value.get_attribute.return_value = "내용"
                checkbox = MagicMock()
                checkbox.is_selected.return_value = True
                driver.find_elements.return_value = [checkbox] if available else []
                editor = PlatoPostEditor(
                    types.SimpleNamespace(driver=driver, safe_get=MagicMock()), temp
                )
                editor._attachment_links = MagicMock(return_value=[])
                editor._file_names = MagicMock(return_value=[])
                editor._open_edit = MagicMock()
                with patch("together.notices.intelligence.post_editor.WebDriverWait"):
                    snapshot = editor.read(url)
                self.assertEqual(snapshot["notice"], True if available else None)
