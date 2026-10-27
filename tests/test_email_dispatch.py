"""발송 응답 직후의 이력 조회와 여러 요청의 공통 자료를 확인한다."""

import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from together.plato.email_dispatch import SharedEmailHistory
from together.plato.notifications import (
    PlatoNotificationTransport,
    request_deadline,
    BASE,
    COURSE,
)


class DispatchTests(unittest.TestCase):
    def response(self, text=""):
        response = MagicMock(status_code=200, text=text)
        response.__enter__.return_value = response
        return response

    def test_receipt_after_fast_post_bypasses_preflight_cache(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = self.response()
        reader = SharedEmailHistory({})
        old = {"eid": "1", "subject": "이전", "student_ids": {"202129133"}}
        new = {"eid": "2", "subject": "이번", "student_ids": {"202129133"}}
        with (
            patch("together.plato.email_dispatch.new_session", return_value=session),
            patch(
                "together.plato.email_dispatch.parse_email_history",
                side_effect=[[old], [new, old]],
            ),
        ):
            self.assertEqual(reader(), [old])
            self.assertEqual(reader(), [old])
            self.assertEqual(reader(fresh=True), [new, old])
            self.assertEqual(session.get.call_count, 2)

    def test_receipts_added_together_are_read_across_history_pages(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = [
            self.response(),
            self.response(
                '<a href="email_log.php?id=157301&amp;page=1">다음</a><a href="https://example.invalid/email_log.php?id=157301&amp;page=2">외부</a>'
            ),
            self.response(),
        ]
        rows = [
            {"eid": str(i), "subject": str(i), "student_ids": {"202129133"}}
            for i in (1, 2, 3)
        ]
        reader = SharedEmailHistory({})
        with (
            patch("together.plato.email_dispatch.new_session", return_value=session),
            patch(
                "together.plato.email_dispatch.parse_email_history",
                side_effect=[[rows[0]], [rows[2]], [rows[1], rows[0]]],
            ),
        ):
            reader()
            self.assertEqual(
                [row["eid"] for row in reader(fresh=True)], ["3", "2", "1"]
            )
            self.assertEqual(session.get.call_count, 3)
            self.assertTrue(
                all(
                    call.args[0].startswith(BASE) for call in session.get.call_args_list
                )
            )

    def test_late_receipt_is_found_even_after_a_newer_known_row(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = [
            self.response(),
            self.response('<a href="email_log.php?id=157301&amp;page=1">다음</a>'),
            self.response(),
        ]
        rows = [
            {"eid": str(i), "subject": str(i), "student_ids": {"202129133"}}
            for i in (1, 3, 4, 5)
        ]
        reader = SharedEmailHistory({})
        with (
            patch("together.plato.email_dispatch.new_session", return_value=session),
            patch(
                "together.plato.email_dispatch.parse_email_history",
                side_effect=[
                    [rows[2], rows[0]],
                    [rows[3], rows[2]],
                    [rows[1], rows[0]],
                ],
            ),
        ):
            reader()
            reader.floor = 1
            self.assertEqual(
                [row["eid"] for row in reader(fresh=True)], ["5", "4", "3", "1"]
            )

    def test_preflight_does_not_wait_for_another_receipt_lookup(self):
        reader = SharedEmailHistory({})
        reader.initialized = True
        results = []
        with reader.lock:
            thread = threading.Thread(target=lambda: results.append(reader()))
            thread.start()
            thread.join(timeout=1)
            completed = not thread.is_alive()
        thread.join(timeout=1)
        self.assertTrue(completed)
        self.assertEqual(results, [[]])

    def test_thread_uses_socket_timeout_without_installing_process_alarm(self):
        completed = []

        def task():
            with request_deadline(45):
                completed.append(True)

        with patch("together.plato.notifications.signal.signal") as signal:
            thread = threading.Thread(target=task)
            thread.start()
            thread.join()
            self.assertEqual(completed, [True])
            signal.assert_not_called()

    def test_snapshot_reuses_validated_form_without_fetching_another_page(self):
        with tempfile.TemporaryDirectory() as temp:
            client = PlatoNotificationTransport.from_session(MagicMock(), temp)
            client.form_snapshot = {
                "message": (
                    {"type": "message_send_group", "courseid": COURSE},
                    [
                        {
                            "id": "384408",
                            "title": "박순홍 (202129133)",
                            "text": "박순홍 (202129133)",
                        }
                    ],
                )
            }
            try:
                _, ids, missing = client.prepare(
                    "message", [{"name": "박순홍", "student_id": "202129133"}]
                )
                self.assertEqual((ids, missing), (["384408"], []))
                self.assertEqual(client.student_ids, {"384408": "202129133"})
                client.session.get.assert_not_called()
            finally:
                client.close()


class RecoveryTests(unittest.TestCase):
    def test_course_reentry_restores_login_after_a_browser_timeout(self):
        from together.plato.service import WriteNoticeService

        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = MagicMock()
        service.driver.get.side_effect = [TimeoutError, None]
        service.driver.find_elements.return_value = []
        service.plato_id = "test"
        service.plato_pw = "test"
        with (
            patch.object(service, "restart_driver"),
            patch.object(service, "login_ready"),
            patch.object(service, "login") as login,
            patch("together.plato.service.time.sleep"),
        ):
            service.move_to_course(
                "https://dev-plato.pusan.ac.kr/course/view.php?id=157301"
            )
            login.assert_called_once_with("test", "test")

    def test_board_connection_is_retried_before_its_number_is_kept(self):
        from together.crawler import announcements as crawler

        response = MagicMock()
        with (
            patch.object(
                crawler,
                "http_get",
                side_effect=[crawler.requests.ConnectTimeout, response],
            ) as get,
            patch.object(crawler.time, "sleep"),
        ):
            self.assertIs(
                crawler._get_board_response("https://example.invalid"), response
            )
            self.assertEqual(get.call_count, 2)


class IndependentLoginTests(unittest.TestCase):
    def test_native_login_uses_the_observed_form_and_browser_headers(self):
        from together.plato.auth import http_login_session

        session = MagicMock()

        def response(text, url):
            result = MagicMock(text=text, url=url)
            result.__enter__.return_value = result
            return result

        root = "https://dev-plato.pusan.ac.kr/"
        session.get.return_value = response(
            '<form method="post" action="/login/index.php"><input name="username"><input name="password"><input name="loginbutton" type="submit" value="로그인"></form>',
            root,
        )
        session.post.return_value = response(
            '<a href="/login/logout.php">로그아웃</a>', root
        )
        with (
            patch("together.plato.auth.requests.Session", return_value=session),
            patch.dict("os.environ", {"PLATO_ID": "test", "PLATO_PW": "test"}),
        ):
            self.assertIs(http_login_session(), session)
            sent = session.post.call_args.kwargs
            self.assertEqual(sent["data"]["loginbutton"], "로그인")
            self.assertEqual(sent["headers"]["Referer"], root)
            self.assertEqual(sent["headers"]["Origin"], root.rstrip("/"))

    def test_login_never_sends_credentials_to_another_host(self):
        from together.plato.auth import http_login_session
        from together.plato.notifications import NotificationAuthenticationError

        session = MagicMock()
        response = MagicMock(
            text='<form method="post" action="https://example.invalid/login/index.php"><input name="username"><input name="password"></form>',
            url="https://dev-plato.pusan.ac.kr/",
        )
        response.__enter__.return_value = response
        session.get.return_value = response
        with patch("together.plato.auth.requests.Session", return_value=session):
            with self.assertRaises(NotificationAuthenticationError):
                http_login_session()
            session.post.assert_not_called()
            session.close.assert_called_once()

    def test_each_email_uses_its_own_form_fields_and_shared_recipient_directory(self):
        from together.plato.email_dispatch import transport_from_snapshot

        session = MagicMock()
        html = '<form method="post" action="action.php"><input type="hidden" name="type" value="email_send"><input type="hidden" name="courseid" value="157301"><input type="hidden" name="attachments" value="987"></form>'
        response = MagicMock(text=html, status_code=200)
        response.__enter__.return_value = response
        session.get.return_value = response
        targets = [
            {
                "id": "42",
                "text": "김학생 (a@example.invalid)",
                "title": "김학생 (a@example.invalid)",
            }
        ]
        snapshot = {
            "forms": {"email": ({"attachments": "123"}, targets)},
            "student_ids": {"42": "202600001"},
            "history": MagicMock(),
        }
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "together.plato.email_dispatch.http_login_session", return_value=session
            ),
        ):
            client = transport_from_snapshot(snapshot, temp)
            try:
                self.assertEqual(client.form_snapshot["email"][0]["attachments"], "987")
                self.assertIs(client.form_snapshot["email"][1], targets)
                self.assertEqual(snapshot["forms"]["email"][0]["attachments"], "123")
                self.assertTrue(client.background_dispatch)
            finally:
                client.close()
