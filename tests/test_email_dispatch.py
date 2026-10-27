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
