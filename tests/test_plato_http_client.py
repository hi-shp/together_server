from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from together.plato.http_client import HttpSubmissionUnknown, PlatoHttpClient
from together.plato.service import WriteNoticeService


class Response:
    def __init__(self, url, text="", content=None, content_type="text/html"):
        self.url = url
        self.text = text
        self.content = content if content is not None else text.encode()
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def close(self):
        return None


class PlatoHttpClientTests(unittest.TestCase):
    def test_cached_login_cookie_is_private_scoped_and_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.json"
            original = requests.Session()
            original.cookies.set(
                "session",
                "private-test-value",
                domain="dev-plato.pusan.ac.kr",
                path="/",
                secure=True,
            )
            original.cookies.set(
                "foreign", "ignored", domain="example.invalid", path="/"
            )
            PlatoHttpClient._save_cookies(original, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            cached = requests.Session()
            cached.get = lambda *args, **kwargs: Response(
                "https://dev-plato.pusan.ac.kr/course/view.php?id=157301",
                '<a href="/login/logout.php?sesskey=x">logout</a>',
            )
            with patch(
                "together.plato.http_client.requests.Session", return_value=cached
            ):
                restored = PlatoHttpClient._cached_session(path)
            self.assertIs(restored, cached)
            self.assertEqual(restored.cookies.get("session"), "private-test-value")
            self.assertIsNone(restored.cookies.get("foreign"))

    def test_expired_cached_session_reauthenticates_only_on_safe_get(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.json"
            expired = requests.Session()
            expired.get = lambda *args, **kwargs: Response(
                "https://dev-plato.pusan.ac.kr/login/index.php",
                '<input id="input-username">',
            )
            fresh = requests.Session()
            fresh.cookies.set(
                "session",
                "fresh-test-value",
                domain="dev-plato.pusan.ac.kr",
                path="/",
            )
            fresh.get = lambda *args, **kwargs: Response(
                "https://dev-plato.pusan.ac.kr/user/users.php?id=157301",
                '<td class="cell c1 number text-center column-number">2,740</td>',
            )
            client = PlatoHttpClient(expired, path)
            with patch(
                "together.plato.http_client.http_login_session", return_value=fresh
            ) as login:
                self.assertEqual(client.participant_count(), 2740)
            login.assert_called_once_with()
            self.assertEqual(client.session.cookies.get("session"), "fresh-test-value")

    def test_participant_count_and_displayed_count_are_read_every_cycle(self):
        session = Session(
            [
                Response(
                    "https://dev-plato.pusan.ac.kr/user/users.php?id=157301",
                    '<td class="cell c1 number text-center column-number">2,741</td>',
                ),
                Response(
                    "https://dev-plato.pusan.ac.kr/course/view.php?id=157301",
                    "현재 2,740명이 함께하고 있습니다.",
                ),
            ]
        )
        client = PlatoHttpClient(session)
        self.assertEqual(client.participant_count(), 2741)
        self.assertEqual(client.displayed_participant_count(), 2740)
        self.assertEqual(len(session.calls), 2)

    def test_course_board_link_is_reused_without_second_request(self):
        html = '''<a href="/mod/ubboard/view.php?id=7"><span class="instancename">교육/특강/프로그램 게시판</span></a>'''
        session = Session(
            [Response("https://dev-plato.pusan.ac.kr/course/view.php?id=157301", html)]
        )
        client = PlatoHttpClient(session)
        self.assertEqual(
            client.board_url("교육/특강/프로그램"),
            "https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=7",
        )
        self.assertEqual(client.displayed_participant_count(), None)
        self.assertEqual(len(session.calls), 1)

    def test_survey_download_keeps_existing_three_csv_contract(self):
        view = '''
        <div class="feedback_info_value">571</div>
        <a href="/mod/feedback/show_entries.php?id=9">응답 보기</a>
        '''
        detail = '''
        <form action="/mod/feedback/show_entries.php" method="get">
          <input type="hidden" name="id" value="9">
          <input type="hidden" name="sesskey" value="token">
          <select name="download"><option value="csv">CSV</option></select>
        </form>
        '''
        csv = (
            "이름,학번,(선택/복수 가능) 학과,(선택/복수 가능) 알림 설정,이메일 주소\n"
            '학생,202600001,"기계공학부(학부)","Plato 쪽지\n이메일",student@example.invalid\n'
        ).encode("utf-8-sig")
        session = Session(
            [
                Response("https://dev-plato.pusan.ac.kr/mod/feedback/view.php", view),
                Response("https://dev-plato.pusan.ac.kr/mod/feedback/show_entries.php", detail),
                Response(
                    "https://dev-plato.pusan.ac.kr/mod/feedback/show_entries.php",
                    content=csv,
                    content_type="text/csv",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            count = PlatoHttpClient(session).download_survey(directory)
            self.assertEqual(count, "571")
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()},
                {"알림 신청하기.csv", "message.csv", "email.csv"},
            )
            message = pd.read_csv(Path(directory) / "message.csv", dtype=str)
            email = pd.read_csv(Path(directory) / "email.csv", dtype=str)
            self.assertEqual(message.loc[0, "학번"], "202600001")
            self.assertEqual(message.loc[0, "Plato 쪽지 여부"], "True")
            self.assertEqual(email.loc[0, "Email 여부"], "True")
        self.assertEqual(session.calls[-1][2]["params"]["download"], "csv")

    def test_create_notice_posts_the_existing_form_and_verifies_article_url(self):
        course = '''
        <a href="/mod/ubboard/view.php?id=7">
          <span class="instancename">교육/특강/프로그램 게시판</span>
        </a>
        '''
        board = '<a href="/mod/ubboard/write.php?id=7">쓰기</a>'
        editor = '''
        <form action="/mod/ubboard/action.php" method="post">
          <input type="hidden" name="id" value="7">
          <input type="hidden" name="sesskey" value="token">
          <input name="subject" value="">
          <textarea name="content[text]"></textarea>
          <input type="hidden" name="content[format]" value="1">
          <input type="submit" name="submitbutton" value="저장">
        </form>
        '''
        article = '''
        <h2>⭐새 공지</h2>
        <a href="https://example.invalid/notice">본문 링크</a>
        '''
        session = Session(
            [
                Response("https://dev-plato.pusan.ac.kr/course/view.php?id=157301", course),
                Response("https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=7", board),
                Response("https://dev-plato.pusan.ac.kr/mod/ubboard/write.php?id=7", editor),
                Response(
                    "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=7&bwid=11",
                    article,
                ),
            ]
        )
        with PlatoHttpClient(session).create_notice(
            "교육/특강/프로그램",
            "⭐새 공지",
            '<p>본문 링크 : <a href="https://example.invalid/notice">주소</a></p>',
        ) as response:
            self.assertIn("⭐새 공지", response.text)
        method, url, kwargs = session.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://dev-plato.pusan.ac.kr/mod/ubboard/action.php")
        self.assertEqual(kwargs["data"]["subject"], "⭐새 공지")
        self.assertIn("example.invalid/notice", kwargs["data"]["content[text]"])

    def test_create_notice_never_retries_after_an_unconfirmed_post(self):
        class FailingSession(Session):
            def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs))
                raise __import__("requests").Timeout("unknown")

        session = FailingSession(
            [
                Response(
                    "https://dev-plato.pusan.ac.kr/course/view.php?id=157301",
                    '<a href="/mod/ubboard/view.php?id=7"><span class="instancename">장학금</span></a>',
                ),
                Response(
                    "https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=7",
                    '<a href="/mod/ubboard/write.php?id=7">쓰기</a>',
                ),
                Response(
                    "https://dev-plato.pusan.ac.kr/mod/ubboard/write.php?id=7",
                    '<form action="/mod/ubboard/action.php"><input name="subject"><textarea name="content[text]"></textarea></form>',
                ),
            ]
        )
        with self.assertRaises(HttpSubmissionUnknown):
            PlatoHttpClient(session).create_notice("장학금", "제목", "<p>본문</p>")
        self.assertEqual([call[0] for call in session.calls].count("POST"), 1)

    def test_service_uses_http_without_starting_browser_for_no_attachment(self):
        article = Response(
            "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=7&bwid=11",
            '<h2>⭐새 공지</h2><a href="https://example.invalid/notice">본문 링크</a>',
        )

        class Http:
            def __init__(self):
                self.calls = []

            def create_notice(self, board, subject, body):
                self.calls.append((board, subject, body))
                return article

        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = None
        service._http = Http()
        with patch.dict(
            "os.environ",
            {"PLATO_HTTP_POST_MODE": "enabled", "POST_VALIDATION_MODE": "off"},
        ):
            receipt = service.write_notice_in_board(
                "새 공지",
                "https://example.invalid/notice",
                "<p>내용</p>",
                [],
                "교육/특강/프로그램",
                "테스트학과",
            )
        self.assertEqual(receipt["state"], "verified")
        self.assertIsNone(service.driver)
        self.assertEqual(service._http.calls[0][0], "교육/특강/프로그램")

    def test_write_notices_keeps_input_order_independent_of_attachments(self):
        calls = []
        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = None

        def write(subject, url, content, files, board, department):
            calls.append((subject, list(files)))
            return {"subject": subject}

        service.write_notice_in_board = write

        class Notice:
            def __init__(self, title, files):
                self.title = title
                self.url = "https://example.invalid/" + title
                self.content_html = "<p>내용</p>"
                self.files = files
                self.notice_board_name = "교육/특강/프로그램"

        service.write_notices(
            "https://dev-plato.pusan.ac.kr/course/view.php?id=157301",
            [Notice("첨부 글", ["a.pdf"]), Notice("일반 글", [])],
            "테스트학과",
        )
        self.assertEqual(calls, [("첨부 글", ["a.pdf"]), ("일반 글", [])])


if __name__ == "__main__":
    unittest.main()
