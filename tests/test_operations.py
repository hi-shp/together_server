"""첨부 보관, 게시 검증, 전송 중복 방지와 장애 기준을 검사한다."""

from datetime import datetime
import json, os, sqlite3, tempfile, time, types, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["POST_VALIDATION_MODE"] = "off"
from together.storage.attachments import AttachmentCache
from together.storage.api_usage import ApiAccounting, DailyApiBudgetExceeded
from together.notices.intelligence.quality import (
    display_title,
    title_problems,
    assess,
    inspect_rendered_post,
    sanitize_html,
)
from together.notices.intelligence.post_editor import set_post_fields
from together.notices.intelligence.publication import known_receipt, remember_receipt
from together.plato.notifications import (
    BASE,
    COURSE,
    PlatoNotificationTransport,
    DeliveryLedger,
    parse_send_form,
    resolve_recipients,
    parse_email_history,
    NotificationError,
    NotificationOutcomeUnknown,
    response_is_success,
)
from tools.health_check import evaluate, delivery_metrics


def form(kind="message", uid="42", name="김학생", identity="202600001", course=COURSE):
    values = {"message": "message_send_group", "email": "email_send"}
    return f"""<form action="action.php" method="post"><input type="hidden" name="type" value="{values[kind]}">
      <input type="hidden" name="courseid" value="{course}"><input type="checkbox" id="user-{uid}" name="tousers[]" value="{uid}">
      <label for="user-{uid}" title="{name} ({identity})">{name} ({identity})</label></form>"""


class Response:
    def __init__(self, text, status=200, url=BASE + "send.php"):
        self.text = text
        self.status_code = status
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CacheTests(unittest.TestCase):
    def test_streamed_downloads_keep_different_files_with_the_same_name(self):
        from together.crawler import announcements as crawler

        source = MagicMock()
        source.url = "https://example.invalid/view/1"
        source.text = '<h2 class="artclViewTitle">교육 안내</h2><div class="artclView">본문</div><dd class="artclInsert"><li><a href="/download.do?f=1">자료.pdf</a></li><li><a href="/download.do?f=2">자료.pdf</a></li></dd>'
        first = MagicMock()
        first.__enter__.return_value = first
        first.iter_content.return_value = iter([b"one", b" data"])
        second = MagicMock()
        second.__enter__.return_value = second
        second.iter_content.return_value = iter([b"two data"])
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                with (
                    patch.object(crawler, "http_get", return_value=source),
                    patch.object(
                        crawler.public_http, "get", side_effect=[first, second]
                    ),
                ):
                    announcement = crawler.crawl_ann(source.url, "교육/특강/프로그램")
                self.assertEqual(len(announcement.files), 2)
                self.assertEqual(
                    {Path(path).read_bytes() for path in announcement.files},
                    {b"one data", b"two data"},
                )
                self.assertEqual(list(Path("downloads").glob(".download-*")), [])
            finally:
                os.chdir(before)

    def test_identical_files_share_one_object(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / "a.pdf"
            b = Path(temp) / "b.pdf"
            a.write_bytes(b"payload")
            b.write_bytes(b"payload")
            cache = AttachmentCache(temp, max_bytes=100, min_free=0)
            self.assertEqual(cache.add(a)["sha256"], cache.add(b)["sha256"])
            self.assertEqual(cache.maintain()["objects"], 1)
            self.assertEqual(a.read_bytes(), b"payload")

    def test_age_and_capacity_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = AttachmentCache(temp, max_bytes=8, min_free=0)
            first = Path(temp) / "one"
            first.write_bytes(b"12345")
            sha = cache.add(first)["sha256"]
            second = Path(temp) / "two"
            second.write_bytes(b"67890")
            cache.add(second)
            self.assertFalse((cache.objects / sha).exists())
            self.assertEqual(cache.maintain()["bytes"], 5)
            with cache.transaction() as db:
                db.execute("UPDATE objects SET last_seen=?", (time.time() - 8 * 86400,))
            self.assertEqual(cache.maintain()["objects"], 0)

    def test_free_space_floor_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file"
            path.write_bytes(b"important")
            cache = AttachmentCache(temp, max_bytes=100, min_free=10**18)
            self.assertFalse(cache.add(path)["stored"])
            self.assertEqual(path.read_bytes(), b"important")


class QualityTests(unittest.TestCase):
    def test_nested_markup_preserves_content_and_resolves_original_links(self):
        from bs4 import BeautifulSoup

        value = '<form><button>홍보</button></form><p onclick="bad()" style="display:none">교육 안내</p><a href="../files/a.pdf">자료</a><img src="/poster.png"><a href="javascript:bad()">오류</a>'
        soup = BeautifulSoup(
            sanitize_html(value, "https://example.invalid/board/view/1"), "html.parser"
        )
        self.assertEqual(soup.p.get_text(), "교육 안내")
        self.assertIsNone(soup.p.get("style"))
        self.assertIsNone(soup.find("form"))
        self.assertIsNone(soup.p.get("onclick"))
        self.assertEqual(soup.a["href"], "https://example.invalid/board/files/a.pdf")
        self.assertEqual(soup.img["src"], "https://example.invalid/poster.png")
        self.assertIsNone(soup.find_all("a")[1].get("href"))

    def test_title_controls_angles_and_non_bmp(self):
        self.assertEqual(
            display_title("\u200b <공지> 🔎 신입 모집\x00"), "＜공지＞ 🔎 신입 모집"
        )
        self.assertTrue(title_problems("\u200b\u200d"))
        self.assertTrue(title_problems("제목\ufffd"))

    def test_personal_sale_is_separate_from_legitimate_hospitality_job(self):
        ann = types.SimpleNamespace(
            title="개인 중고 팝니다", content_text="연락 주세요"
        )
        self.assertFalse(assess(ann)["ok"])
        ann.title = "마카오 카지노 호텔 직원 채용"
        ann.content_text = "정규직 직원을 모집합니다."
        self.assertTrue(assess(ann)["ok"])

    def test_existing_semantic_review_is_reused(self):
        ann = types.SimpleNamespace(title="방송 후기", content_text="연예인 개인 소식")
        self.assertFalse(assess(ann, {"notice_type": "personal_or_ad"})["ok"])

    def test_rendered_hidden_title_and_missing_body_are_detected(self):
        driver = MagicMock()
        driver.execute_script.return_value = {
            "title": "제목",
            "title_visible": False,
            "body": "",
            "body_visible": False,
            "links": [],
            "images": [],
        }
        result = inspect_rendered_post(
            driver, "제목", "본문", "https://example.invalid"
        )
        self.assertIn("rendered_title_mismatch", result["reasons"])
        self.assertIn("rendered_body_empty", result["reasons"])

    def test_rendered_paragraph_spacing_is_accepted(self):
        driver = MagicMock()
        driver.execute_script.return_value = {
            "title": "제목",
            "title_visible": True,
            "body": "첫 문단\n다음 문단",
            "body_visible": True,
            "links": ["https://example.invalid"],
            "images": [],
        }
        self.assertTrue(
            inspect_rendered_post(
                driver, "제목", "첫 문단 다음 문단", "https://example.invalid"
            )["ok"]
        )

    def test_field_sync_is_independent_of_uploads(self):
        driver = MagicMock()
        set_post_fields(driver, "🔎 제목", "<p>본문</p>")
        driver.execute_script.assert_called_once()
        self.assertEqual(driver.execute_script.call_args.args[1], "🔎 제목")

    def test_created_receipt_survives_a_later_validation_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = {
                "source_url": "https://example.invalid/1",
                "article_url": "https://example.invalid/post",
                "subject": "제목",
                "state": "created",
                "_snapshot": {"large": "private"},
            }
            remember_receipt(temp, receipt)
            read = known_receipt(temp, receipt["source_url"])
            self.assertEqual(read["state"], "created")
            self.assertNotIn("_snapshot", read)


class TransportTests(unittest.TestCase):
    def test_queued_recipient_snapshot_must_match_before_posting(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            try:
                with self.assertRaises(NotificationError):
                    client.send("message", [], "", "본문", "event", expected_ids=["99"])
                client.session.post.assert_not_called()
            finally:
                client.close()

    def test_transient_form_read_is_retried_without_retrying_a_send(self):
        import requests

        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            response = Response("form")
            client.session.get.side_effect = [requests.ConnectionError(), response]
            try:
                with patch("time.sleep"):
                    self.assertIs(client._get(BASE + "send.php", timeout=30), response)
                self.assertEqual(client.session.get.call_count, 2)
                client.session.post.assert_not_called()
            finally:
                client.close()

    def test_restart_recovers_a_late_email_receipt_without_another_post(self):
        import requests

        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            client.student_ids = {"42": "202600001"}
            client.session.post.side_effect = requests.ReadTimeout()
            client.email_history = MagicMock(side_effect=[[], []])
            try:
                with self.assertRaises(NotificationOutcomeUnknown):
                    client.send("email", [], "제목", "본문", "event")
            finally:
                client.close()
            resumed = self.build(temp)
            resumed.email_history = MagicMock(
                return_value=[
                    {"eid": "2", "subject": "제목", "student_ids": {"202600001"}}
                ]
            )
            try:
                self.assertEqual(
                    resumed.send("email", [], "제목", "본문", "event")["deduplicated"],
                    1,
                )
                resumed.session.post.assert_not_called()
            finally:
                resumed.close()

    def test_all_recipients_are_sent_in_one_request(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp, "199명에게 메세지가 전달되었습니다.")
            ids = [str(i) for i in range(1, 200)]
            client.prepare.return_value = ({}, ids, [])
            try:
                result = client.send("message", [], "", "본문", "event")
                self.assertEqual(result["sent"], 199)
                client.session.post.assert_called_once()
                payload = client.session.post.call_args.kwargs["data"]
                self.assertEqual(
                    [value for key, value in payload if key == "tousers[]"], ids
                )
            finally:
                client.close()

    def test_legacy_batches_are_not_resent_after_switching_to_one_request(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            ids = [str(i) for i in range(1, 200)]
            client.prepare.return_value = ({}, ids, [])
            for batch in (ids[:100], ids[100:]):
                key = hashlib.sha256(
                    json.dumps(["message", "event", batch], ensure_ascii=False).encode()
                ).hexdigest()
                client.ledger.claim(key, "message", len(batch))
                client.ledger.finish(key, "sent", 1)
            try:
                self.assertEqual(
                    client.send("message", [], "", "본문", "event")["deduplicated"], 199
                )
                client.session.post.assert_not_called()
            finally:
                client.close()

    def test_changed_recipient_order_does_not_resend_existing_users(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp, "2명에게 메세지가 전달되었습니다.")
            client.prepare.return_value = ({}, ["1", "2"], [])
            try:
                client.send("message", [], "", "본문", "event")
                client.prepare.return_value = ({}, ["2", "1", "3"], [])
                client.session.post.return_value = Response(
                    "1명에게 메세지가 전달되었습니다."
                )
                result = client.send("message", [], "", "본문", "event")
                self.assertEqual((result["sent"], result["deduplicated"]), (1, 2))
                self.assertEqual(
                    [
                        value
                        for key, value in client.session.post.call_args.kwargs["data"]
                        if key == "tousers[]"
                    ],
                    ["3"],
                )
            finally:
                client.close()

    def test_timeout_is_reconciled_from_new_email_history_without_resending(self):
        import requests

        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            client.student_ids = {"42": "202600001"}
            client.session.post.side_effect = requests.ReadTimeout()
            receipt = {"eid": "2", "subject": "제목", "student_ids": {"202600001"}}
            client.email_history = MagicMock(side_effect=[[], [receipt]])
            try:
                self.assertEqual(
                    client.send("email", [], "제목", "본문", "event")["sent"], 1
                )
                self.assertEqual(
                    client.send("email", [], "제목", "본문", "event")["deduplicated"], 1
                )
                client.session.post.assert_called_once()
            finally:
                client.close()

    def test_email_preflight_failure_does_not_claim_or_send(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            client.student_ids = {"42": "202600001"}
            client.email_history = MagicMock(
                side_effect=NotificationError("history unavailable")
            )
            try:
                with self.assertRaises(NotificationError):
                    client.send("email", [], "제목", "본문", "event")
                client.session.post.assert_not_called()
                self.assertEqual(
                    client.ledger.connection.execute(
                        "SELECT COUNT(*) FROM deliveries"
                    ).fetchone()[0],
                    0,
                )
            finally:
                client.close()

    def test_email_requires_new_history_with_matching_subject_and_students(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp, "redirect")
            client.student_ids = {"42": "202600001"}
            old = {"eid": "1", "subject": "제목", "student_ids": {"202600001"}}
            wrong = {"eid": "2", "subject": "제목", "student_ids": {"202600002"}}
            client.email_history = MagicMock(
                side_effect=[[old], [old, wrong], [old, wrong]]
            )
            try:
                with self.assertRaises(NotificationOutcomeUnknown):
                    client.send("email", [], "제목", "본문", "event")
                with self.assertRaises(NotificationOutcomeUnknown):
                    client.send("email", [], "제목", "본문", "event")
                client.session.post.assert_called_once()
            finally:
                client.close()

    def test_new_email_receipt_and_unknown_history_after_post(self):
        for unavailable in (False, True):
            with (
                self.subTest(unavailable=unavailable),
                tempfile.TemporaryDirectory() as temp,
            ):
                client = self.build(temp, "redirect")
                client.student_ids = {"42": "202600001"}
                new = {"eid": "2", "subject": "제목", "student_ids": {"202600001"}}
                client.email_history = MagicMock(
                    side_effect=[
                        [],
                        NotificationError("timeout") if unavailable else [new],
                    ]
                )
                try:
                    if unavailable:
                        with self.assertRaises(NotificationOutcomeUnknown):
                            client.send("email", [], "제목", "본문", "event")
                        state = client.ledger.connection.execute(
                            "SELECT state FROM deliveries"
                        ).fetchone()[0]
                        self.assertEqual(state, "unknown")
                    else:
                        self.assertEqual(
                            client.send("email", [], "제목", "본문", "event")["sent"], 1
                        )
                    client.session.post.assert_called_once()
                finally:
                    client.close()

    def test_scope_and_exact_recipient_validation(self):
        hidden, targets = parse_send_form(form(), "message", BASE + "send.php")
        self.assertEqual(
            resolve_recipients(
                targets, [{"name": "김학생", "student_id": "202600001"}], "message"
            ),
            (["42"], []),
        )
        ids, missing = resolve_recipients(
            targets, [{"name": "다른학생", "student_id": "202600001"}], "message"
        )
        self.assertEqual(ids, [])
        self.assertEqual(len(missing), 1)
        with self.assertRaises(NotificationError):
            parse_send_form(form(course="999"), "message", BASE + "send.php")

    def test_server_reply_and_email_log_identity(self):
        self.assertTrue(
            response_is_success(
                Response('<script>alert("1명에게 메세지가 전달되었습니다.")</script>')
            )
        )
        history = """<table><tr><td>날짜</td><td>테스트 제목</td><td><span title="STUDENT (202600001)">STUDENT</span></td><td></td>
          <td><a href="https://dev-plato.pusan.ac.kr/local/ubmessage/email_send.php?id=157301&amp;eid=99">Resend</a></td></tr></table>"""
        self.assertEqual(parse_email_history(history)[0]["student_ids"], {"202600001"})

    def build(self, temp, response="1명에게 메세지가 전달되었습니다."):
        client = PlatoNotificationTransport.__new__(PlatoNotificationTransport)
        client.base_dir = Path(temp)
        (Path(temp) / ".runtime").mkdir(exist_ok=True)
        client.ledger = DeliveryLedger(Path(temp) / ".runtime/ledger.sqlite3")
        client.student_ids = {}
        client.prepare = MagicMock(
            return_value=(
                {"type": "message_send_group", "courseid": COURSE},
                ["42"],
                [],
            )
        )
        client.session = MagicMock()
        client.session.post.return_value = Response(response)
        return client

    def test_retry_of_confirmed_delivery_sends_once(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            try:
                one = client.send("message", [], "", "본문", "event")
                two = client.send("message", [], "", "본문", "event")
                self.assertEqual(one["sent"], 1)
                self.assertEqual(two["deduplicated"], 1)
                client.session.post.assert_called_once()
            finally:
                client.close()

    def test_unknown_delivery_and_partial_reply_are_not_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp, "2명에게 메세지가 전달되었습니다.")
            try:
                with self.assertRaises(NotificationOutcomeUnknown):
                    client.send("message", [], "", "본문", "event")
                with self.assertRaises(NotificationOutcomeUnknown):
                    client.send("message", [], "", "본문", "event")
                client.session.post.assert_called_once()
            finally:
                client.close()

    def test_self_test_cannot_send_to_another_recipient(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.build(temp)
            try:
                with self.assertRaises(NotificationError):
                    client.send("message", [], "", "본문", "event", test_only_id="999")
                client.session.post.assert_not_called()
            finally:
                client.close()


class ApiUsageTests(unittest.TestCase):
    def test_balance_snapshot_is_bound_to_the_key(self):
        from tools.health_check import budget_status

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".runtime/health"
            root.mkdir(parents=True)
            (root / "api-budget.json").write_text(
                json.dumps({"key_fingerprint": "other-key"})
            )
            self.assertEqual(budget_status(temp, {})["balance_source"], "key_changed")

    def test_real_tokens_cost_and_daily_limit(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"GPT_DAILY_BUDGET_USD": "0.0001"}),
        ):
            accounting = ApiAccounting(temp)
            request = {"response_format": {"json_schema": {"name": "notice_category"}}}
            usage = types.SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=20,
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=300),
            )
            accounting.record(
                "gpt-5.4-mini-2026-03-17",
                request,
                response=types.SimpleNamespace(usage=usage),
            )
            self.assertAlmostEqual(
                accounting.summary()["days"][0]["estimated_usd"], 0.0006375
            )
            with self.assertRaises(DailyApiBudgetExceeded):
                accounting.check_budget()

    def test_identical_requests_are_cached_with_candidate_order(self):
        with tempfile.TemporaryDirectory() as temp:
            accounting = ApiAccounting(temp)
            request = {"messages": [{"role": "user", "content": "자료"}]}
            accounting.store("model", request, {"category": "교육"})
            self.assertEqual(accounting.cached("model", request), {"category": "교육"})
            self.assertIsNone(accounting.cached("other-model", request))


class HealthTests(unittest.TestCase):
    def test_manual_runner_is_not_reported_as_a_stopped_service(self):
        issues, _ = evaluate(self.metrics(expected_running=True, crawler_running=True))
        self.assertEqual(issues, [])

    def metrics(self, **kwargs):
        return dict(
            {
                "free_bytes": 30 * 1024**3,
                "memory_percent": 50,
                "service": "inactive",
                "expected_running": False,
                "runner": {},
            },
            **kwargs,
        )

    def test_intentional_pause_is_not_an_outage(self):
        self.assertEqual(evaluate(self.metrics())[0], [])
        self.assertIn(
            "crawler_stopped",
            [row["code"] for row in evaluate(self.metrics(expected_running=True))[0]],
        )

    def test_memory_and_delivery_alerts_require_significant_conditions(self):
        issues, state = evaluate(self.metrics(memory_percent=97))
        self.assertEqual(issues, [])
        _, state = evaluate(self.metrics(memory_percent=97), state)
        issues, _ = evaluate(self.metrics(memory_percent=97), state)
        self.assertIn("memory_critical", [i["code"] for i in issues])
        self.assertEqual(evaluate(self.metrics(unknown_delivery_recipients=1))[0], [])
        self.assertIn(
            "notification_outcome_unknown",
            [
                i["code"]
                for i in evaluate(self.metrics(unknown_delivery_recipients=100))[0]
            ],
        )

    def test_delivery_metrics_allow_normal_six_minute_email_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / ".runtime"
            runtime.mkdir()
            with sqlite3.connect(runtime / "notification-deliveries.sqlite3") as db:
                db.execute(
                    "CREATE TABLE deliveries (state TEXT, recipients INTEGER, updated REAL)"
                )
                db.executemany(
                    "INSERT INTO deliveries VALUES (?,?,?)",
                    [("sent", 68, 9600), ("sending", 68, 9400)],
                )
            with sqlite3.connect(runtime / "email-outbox.sqlite3") as db:
                db.execute(
                    "CREATE TABLE jobs (state TEXT, recipients INTEGER, created REAL, updated REAL)"
                )
                db.executemany(
                    "INSERT INTO jobs VALUES (?,?,?,?)",
                    [
                        ("pending", 68, 2000, 2000),
                        ("sending", 68, 3000, 9400),
                        ("sent", 68, 4000, 9600),
                    ],
                )
            metrics = delivery_metrics(root, now=10000)
            self.assertEqual(metrics["unknown_delivery_recipients"], 0)
            self.assertEqual(metrics["email_queue_oldest_seconds"], 8000)
            self.assertEqual(metrics["email_queue_idle_seconds"], 400)
            self.assertEqual(
                delivery_metrics(root, now=11700)["unknown_delivery_recipients"], 68
            )

    def test_healthy_email_backlog_is_not_a_stall(self):
        self.assertEqual(
            evaluate(
                self.metrics(
                    email_queue_pending=80,
                    email_queue_oldest_seconds=7200,
                    email_queue_idle_seconds=350,
                    email_worker="active",
                )
            )[0],
            [],
        )
        codes = [
            row["code"]
            for row in evaluate(
                self.metrics(
                    email_queue_pending=80,
                    email_queue_oldest_seconds=7200,
                    email_queue_idle_seconds=1900,
                    email_worker="active",
                )
            )[0]
        ]
        self.assertIn("email_queue_stalled", codes)

    def test_night_pause_and_unverified_forecast_do_not_raise_false_alarms(self):
        metrics = self.metrics(
            service="active",
            runner={
                "status": "night_pause",
                "next_wake": time.time() + 3600,
                "updated_at": time.time() - 4 * 3600,
            },
            budget={
                "forecast_days": None,
                "expires_in_days": 100,
                "estimated_remaining_usd": 3.51,
            },
        )
        self.assertEqual(evaluate(metrics)[0], [])


if __name__ == "__main__":
    unittest.main()
