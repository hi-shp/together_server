"""Zero-match subscriptions must never become successful deliveries."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
from together.plato.notifications import resolve_recipients, PlatoNotificationTransport
from together.plato.service import WriteNoticeService

class RecipientTests(unittest.TestCase):
    targets = [{"id": uid, "title": "학생 (a@example.invalid)", "text": "학생 (a@example.invalid)"} for uid in ["1", "2"]]
    person = {"name": "학생", "email": "a@example.invalid", "student_id": "202600001"}

    def test_ambiguous_email_resolved_only_with_exact_student_identity(self):
        self.assertEqual(resolve_recipients(self.targets, [self.person], "email", {"1": "202600001", "2": "202600002"}), (["1"], []))

    def test_no_arbitrary_selection_for_ambiguous_or_missing_student(self):
        for mapping in [{}, {"1": "other", "2": "another"}, {"1": "202600001", "2": "202600001"}]:
            ids, missing = resolve_recipients(self.targets, [self.person], "email", mapping)
            self.assertEqual(ids, [])
            self.assertEqual(len(missing), 1)

    def test_student_alone_cannot_override_wrong_name_or_email(self):
        p = dict(self.person, email="different@example.invalid")
        self.assertEqual(resolve_recipients(self.targets, [p], "email", {"1": p["student_id"]})[0], [])

    def test_prepare_email_uses_message_directory_and_keeps_email_form(self):
        with tempfile.TemporaryDirectory() as temp:
            client = PlatoNotificationTransport.from_session(MagicMock(), temp)
            hidden = {"type": "email_send"}
            client.form_snapshot = {"email": (hidden, self.targets), "message": ({}, [{"id": "1", "title": "학생 (202600001)", "text": "학생 (202600001)"}])}
            try:
                self.assertEqual(client.prepare("email", [self.person]), (hidden, ["1"], []))
                self.assertIs(client.last_form[0], hidden)
                client.session.get.assert_not_called()
            finally:
                client.close()

    def test_existing_csv_format_is_preserved_and_identity_must_be_unique(self):
        with tempfile.TemporaryDirectory() as home, patch("os.path.expanduser", side_effect=lambda value: home if value == "~" else value):
            d = Path(home) / "Downloads"
            d.mkdir()
            row = {"이름": "학생", "이메일 주소": "a@example.invalid", "(선택/복수 가능) 학과": "화학과", "(선택/복수 가능) 알림 설정": "이메일"}
            pd.DataFrame([row]).to_csv(d / "email.csv", index=False)
            source = pd.DataFrame([dict(row, 학번="202600001")])
            source.to_csv(d / "알림 신청하기.csv", index=False)
            service = WriteNoticeService.__new__(WriteNoticeService)
            before = (d / "email.csv").read_bytes()
            self.assertEqual(service._recipients("email", "화학과")[0]["student_id"], "202600001")
            pd.concat([source, pd.DataFrame([dict(row, 학번="202600002")])]).to_csv(d / "알림 신청하기.csv", index=False)
            self.assertNotIn("student_id", service._recipients("email", "화학과")[0])
            self.assertEqual(before, (d / "email.csv").read_bytes())

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.service = WriteNoticeService.__new__(WriteNoticeService)
        self.service._recipients = MagicMock(return_value=[{"name": "학생", "student_id": "202600001", "email": "a@example.invalid"}])
        self.client = MagicMock()
        self.service._notification = MagicMock(return_value=self.client)
        self.client.send.return_value = {"sent": 0, "deduplicated": 0, "missing": 1}
        self.client.validated_recipients.return_value = ({}, [], ["hashed"])
        self.patches = [patch.dict(os.environ, {"PLATO_NOTIFICATION_TRANSPORT": "http", "PLATO_EMAIL_MODE": "direct"}), patch("together.plato.service.event"), patch("together.plato.service.notify"), patch("together.plato.service.email_content", return_value="body"), patch("together.plato.service.EmailOutbox")]
        _, self.event, self.notify, _, self.outbox = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_unmatched_message_is_not_completed_or_announced(self):
        result = self.service.send_message("title", "https://example.invalid/notice", "dept")
        self.assertEqual(result["state"], "no_matching_recipients")
        self.event.assert_not_called()
        self.notify.assert_not_called()

    def test_unmatched_direct_email_is_not_completed(self):
        self.service.send_email("title", "https://example.invalid/notice", "body", "dept")
        self.event.assert_not_called()
        self.notify.assert_not_called()

    def test_unmatched_queue_email_never_enters_worker(self):
        with patch.dict(os.environ, {"PLATO_EMAIL_MODE": "queue"}):
            self.service.send_email("title", "https://example.invalid/notice", "body", "dept")
        self.outbox.assert_not_called()
        self.client.send.assert_not_called()
        self.notify.assert_not_called()

    def test_sent_and_already_sent_recipients_keep_existing_completion_format(self):
        for sent, previous in [(1, 0), (0, 1)]:
            self.notify.reset_mock()
            self.client.send.return_value = {"sent": sent, "deduplicated": previous, "missing": 1}
            self.service.send_message("title", "https://example.invalid/notice", "dept")
            self.assertEqual(self.event.call_args.args[1], "dept/Plato 쪽지 전송 완료 (1명)")
            self.assertEqual(self.notify.call_count, sent)

    def test_no_subscriptions_remains_silent(self):
        self.service._recipients.return_value = []
        self.service.send_message("title", "https://example.invalid/notice", "dept")
        self.service.send_email("title", "https://example.invalid/notice", "body", "dept")
        self.event.assert_not_called()
        self.client.send.assert_not_called()


class ExclusionTests(unittest.TestCase):
    def test_confirmed_absence_is_remembered_without_personal_data(self):
        from together.storage.recipient_exclusions import RecipientExclusions
        with tempfile.TemporaryDirectory() as temp:
            store = RecipientExclusions(temp)
            absent = {"name": "떠난학생", "student_id": "202600009"}
            targets = [{"id": "1", "title": "재학생 (202600001)", "text": "재학생 (202600001)"}]
            self.assertEqual(store.remember_absent("message", [absent], targets, {"1": "202600001"}), 1)
            self.assertEqual(RecipientExclusions(temp).filter("message", [absent]), [])
            self.assertNotIn(absent["name"].encode(), store.path.read_bytes())
            self.assertNotIn(absent["student_id"].encode(), store.path.read_bytes())

    def test_ambiguous_and_changed_names_are_not_retired(self):
        from together.storage.recipient_exclusions import RecipientExclusions
        with tempfile.TemporaryDirectory() as temp:
            store = RecipientExclusions(temp)
            person = {"name": "학생", "student_id": "202600001", "email": "a@example.invalid"}
            self.assertEqual(store.remember_absent("email", [person], RecipientTests.targets, {}), 0)
            targets = [{"id": "1", "title": "바뀐이름 (202600001)", "text": "바뀐이름 (202600001)"}]
            self.assertEqual(store.remember_absent("message", [person], targets, {}), 0)
            self.assertEqual(store.remember_absent("email", [person], targets, {"1": "202600001"}), 0)
            self.assertFalse(store.path.exists())
