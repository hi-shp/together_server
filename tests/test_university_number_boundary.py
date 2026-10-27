import contextlib, io, os, tempfile, types, unittest, json
from pathlib import Path
from unittest.mock import patch
from together.crawler import announcements as crawler


class UniversityNumberBoundary(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()

    def tearDown(self):
        os.chdir(self.cwd)

    def collect(self, root, body, number=210, page=2):
        response = types.SimpleNamespace(
            text="<table><tbody>" + body + "</tbody></table>",
            url=f"https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page={page}&mgr_seq=3",
            raise_for_status=lambda: None,
        )
        request = types.SimpleNamespace(
            page_url=response.url, number=number, department="대학 공지"
        )
        with (
            patch.object(crawler, "BASE_DIR", root),
            patch.object(crawler, "http_get", return_value=response),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return crawler.get_anns_url(request, 1)

    def root(self, temp):
        root = Path(temp)
        os.chdir(root)
        (root / "yesterday.txt").write_text(
            "최신 일반 기준\n이전 고정\n", encoding="utf-8"
        )
        return root

    def test_old_notice_entering_page_two_after_pin_expiry_is_not_new(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp)
            old = '<tr><td class="num">209</td><td class="subject"><a href="?board_seq=1511715">2026학년도 겨울계절수업 및 도약수업 운영 안내</a></td><td>2026-10-22</td></tr>'
            new = '<tr><td class="num">211</td><td class="subject"><a href="?board_seq=999">오늘 새 글</a></td><td>2026-10-28</td></tr>'
            urls, _ = self.collect(root, new + old)
            self.assertEqual(len(urls), 1)
            self.assertIn("board_seq=999", urls[0])

    def test_number_pin_and_page_moves_do_not_resend_a_registered_notice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp)
            row = (
                lambda mark: f'<tr><td class="num">{mark}</td><td class="subject"><a href="?board_seq=777">같은 행사</a></td></tr>'
            )
            urls, _ = self.collect(root, row("211"))
            self.assertEqual(len(urls), 1)
            folder = root / ".runtime"
            folder.mkdir()
            (folder / "published-receipts.json").write_text(
                json.dumps(
                    {"x": {"source_url": urls[0], "notifications_registered": True}}
                )
            )
            anchor = '<tr><td class="num">210</td><td class="subject"><a href="?board_seq=700">최신 일반 기준</a></td></tr>'
            pinned, _ = self.collect(root, row('<img alt="공지">') + anchor, page=1)
            ordinary, _ = self.collect(root, row("211"), page=2)
            self.assertEqual(pinned, [])
            self.assertEqual(ordinary, [])

    def test_only_a_recorded_unfinished_notice_can_resume_below_the_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp)
            folder = root / ".runtime"
            folder.mkdir()
            canonical = "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1&board_seq=777"
            (folder / "published-receipts.json").write_text(
                json.dumps({"x": {"source_url": canonical, "state": "created"}})
            )
            body = '<tr><td class="num">209</td><td class="subject"><a href="?board_seq=777">이미 게시한 미완료 글</a></td></tr>'
            urls, _ = self.collect(root, body)
            self.assertEqual(urls, [canonical])
