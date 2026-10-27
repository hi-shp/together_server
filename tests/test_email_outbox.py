"""이메일의 독립 처리, 재시작과 기존 완료 문구를 확인한다."""

import contextlib, io, os, tempfile, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from together.storage.email_outbox import EmailOutbox
from together.operations.email_worker import process_job
from together.plato.notifications import NotificationOutcomeUnknown
from together.plato.service import WriteNoticeService


def payload():
    return {
        "title": "공지",
        "url": "https://example.invalid",
        "department": "시험학과",
        "subject": "✔ [시험학과] 공지",
        "body": "기존 HTML",
        "recipients": [{"name": "학생", "email": "student@example.invalid"}],
        "event_key": "event",
    }


class EmailOutboxTests(unittest.TestCase):
    def test_failed_registration_does_not_print_completion(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {"PLATO_NOTIFICATION_TRANSPORT": "http", "PLATO_EMAIL_MODE": "queue"},
            ),
        ):
            service = WriteNoticeService.__new__(WriteNoticeService)
            service._recipients = MagicMock(return_value=payload()["recipients"])
            service._notification = MagicMock()
            service._notification.return_value.validated_recipients.return_value = (
                {},
                ["42"],
                [],
            )
            with (
                patch("together.plato.service.BASE_DIR", Path(temp)),
                patch("together.plato.service.email_content", return_value="기존 HTML"),
                patch.object(EmailOutbox, "enqueue", side_effect=OSError()),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                with self.assertRaises(OSError):
                    service.send_email(
                        "공지", "https://example.invalid", "본문", "시험학과"
                    )
            self.assertEqual(output.getvalue(), "")

    def test_enqueue_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            first = EmailOutbox(temp)
            a = first.enqueue(payload())
            b = first.enqueue(payload())
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(a["state"], "pending")
            restarted = EmailOutbox(temp)
            job = restarted.claim()
            self.assertEqual(job["payload"], payload())
            self.assertIsNone(restarted.claim())
            restarted.recover_interrupted()
            self.assertEqual(restarted.claim()["state"], "unknown")

    def test_crawler_enqueues_without_starting_a_network_request(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {"PLATO_NOTIFICATION_TRANSPORT": "http", "PLATO_EMAIL_MODE": "queue"},
            ),
        ):
            service = WriteNoticeService.__new__(WriteNoticeService)
            service._recipients = MagicMock(return_value=payload()["recipients"])
            transport = MagicMock()
            transport.validated_recipients.return_value = ({}, ["42"], [])
            transport.send.side_effect = AssertionError(
                "Crawler must not wait for email delivery"
            )
            service._notification = MagicMock(return_value=transport)
            with (
                patch("together.plato.service.BASE_DIR", Path(temp)),
                patch("together.plato.service.email_content", return_value="기존 HTML"),
                patch("together.plato.service.notify") as notify,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                result = service.send_email(
                    "공지", "https://example.invalid", "본문", "시험학과"
                )
            self.assertEqual(result["state"], "pending")
            transport.send.assert_not_called()
            self.assertEqual("시험학과/이메일 전송 완료 (1명)\n", output.getvalue())
            notify.assert_called_once_with(
                "시험학과/이메일 전송 완료 (1명) -공지",
                channel="DISCORD_EMAIL_WEBHOOK_URL",
            )

    def test_completed_job_keeps_the_original_console_and_discord_text(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = EmailOutbox(temp)
            outbox.enqueue(payload())
            job = outbox.claim()
            transport = MagicMock()
            transport.send.return_value = {"sent": 68, "deduplicated": 0}
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertTrue(process_job(outbox, job, transport))
            self.assertIn("발송 이력 확인 68명", output.getvalue())
            with outbox.connection() as db:
                row = db.execute("SELECT state,payload FROM jobs").fetchone()
                self.assertEqual(tuple(row), ("sent", "{}"))

    def test_unknown_result_is_checked_without_posting_the_email_again(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = EmailOutbox(temp)
            outbox.enqueue(payload())
            outbox.claim()
            outbox.recover_interrupted()
            job = outbox.claim()
            transport = MagicMock()
            transport.recover_event.return_value = False
            self.assertFalse(process_job(outbox, job, transport))
            transport.send.assert_not_called()
            with outbox.connection() as db:
                self.assertEqual(
                    db.execute("SELECT state FROM jobs").fetchone()[0], "unknown"
                )

    def test_late_receipt_finishes_an_interrupted_job(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = EmailOutbox(temp)
            outbox.enqueue(payload())
            outbox.claim()
            outbox.recover_interrupted()
            job = outbox.claim()
            transport = MagicMock()
            transport.recover_event.return_value = 68
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(process_job(outbox, job, transport))
            transport.send.assert_not_called()
            with outbox.connection() as db:
                self.assertEqual(
                    db.execute("SELECT state FROM jobs").fetchone()[0], "sent"
                )

    def test_unverified_response_stays_in_the_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = EmailOutbox(temp)
            outbox.enqueue(payload())
            job = outbox.claim()
            transport = MagicMock()
            transport.send.side_effect = NotificationOutcomeUnknown()
            self.assertFalse(process_job(outbox, job, transport))
            with outbox.connection() as db:
                self.assertEqual(
                    db.execute("SELECT state FROM jobs").fetchone()[0], "unknown"
                )
