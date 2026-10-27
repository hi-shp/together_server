import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from together.crawler.files import sanitize_filename, download_attachment
from together.operations import events
from together.plato.content import email_content, message_content


class Response:
    def __init__(self, headers=None, body=b"document"):
        self.headers = headers or {}
        self.url = "https://example.invalid/downloadRun.do?qcode=123"
        self.body = body

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class AttachmentNameTests(unittest.TestCase):
    def fetch(self, root, name, response=None):
        with patch(
            "together.crawler.files.requests.get", return_value=response or Response()
        ):
            return download_attachment(
                "https://example.invalid/downloadRun.do?qcode=123",
                name,
                referer="https://example.invalid/notice",
                directory=root,
            )

    def test_parenthesized_form_name_keeps_the_full_name(self):
        with tempfile.TemporaryDirectory() as temp:
            name = "(양식) 2026 겨울계절학기_온디바이스 AI PBL 참여신청서.hwp"
            result = self.fetch(temp, name + " (48KB)")
            self.assertEqual(result["filename"], name)
            self.assertEqual(Path(result["path"]).read_bytes(), b"document")

    def test_empty_label_uses_rfc5987_header(self):
        name = "(양식) 생성형 AI 참여신청서.hwp"
        with tempfile.TemporaryDirectory() as temp:
            response = Response(
                {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)}
            )
            self.assertEqual(self.fetch(temp, "", response)["filename"], name)

    def test_empty_name_has_deterministic_fallback_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            first = self.fetch(temp, "")
            second = self.fetch(temp, "")
            self.assertEqual(first["path"], second["path"])
            self.assertTrue(first["filename"].endswith(".bin"))
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_directory_collision_and_changed_bytes_never_overwrite_existing_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "자료.hwp").mkdir()
            first = self.fetch(temp, "자료.hwp")
            second = self.fetch(temp, "자료.hwp", Response(body=b"changed"))
            self.assertTrue((Path(temp) / "자료.hwp").is_dir())
            self.assertNotEqual(first["path"], second["path"])
            self.assertEqual(Path(first["path"]).read_bytes(), b"document")

    def test_unicode_controls_reserved_names_traversal_and_byte_length(self):
        for value in (
            "../CON.hwp",
            "a\x00\u202eb.hwp",
            "한글😀" * 150 + ".pdf",
            ".",
            "..",
            "",
        ):
            name = sanitize_filename(value)
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn("\u202e", name)
            self.assertNotIn("\x00", name)
            self.assertLessEqual(len(name.encode()), 210)
        self.assertEqual(
            sanitize_filename("(양식) 「자료」 ＆ 😀.hwp"), "(양식) 「자료」 ＆ 😀.hwp"
        )
        self.assertEqual(sanitize_filename("CON.hwp"), "_CON.hwp")

    def test_html_error_response_is_not_saved_as_an_attachment(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                self.fetch(temp, "자료.hwp", Response({"Content-Type": "text/html"}))
            self.assertEqual(list(Path(temp).iterdir()), [])


class DiagnosticsTests(unittest.TestCase):
    def test_records_correlate_notice_and_redact_secrets_without_recipient_fields(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(events, "BASE_DIR", Path(temp)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            events.start_cycle()
            events.set_notice(
                "https://example.invalid/view?id=1&token=hidden", "대학 공지", "제목"
            )
            events.event(
                "첨부",
                "실패 a@example.com",
                level="ERROR",
                error="OSError",
                recipients=["private"],
                source_url="https://example.invalid/file?id=3&sesskey=hidden",
            )
            row = json.loads(
                (Path(temp) / ".runtime/telemetry/events.jsonl").read_text()
            )
            self.assertIn("notice_id", row)
            self.assertIn("cycle_id", row)
            self.assertNotIn("hidden", json.dumps(row))
            self.assertNotIn("a@example.com", output.getvalue())
            self.assertNotIn("recipients", row)
            events.clear_notice()

    def test_storage_failure_does_not_interrupt_processing(self):
        with (
            patch.object(events, "_save", side_effect=OSError),
            patch.object(events, "_warned", False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            events.event("공지", "계속 처리")

    def test_rotated_history_has_a_fixed_size_bound(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(events, "BASE_DIR", Path(temp)),
            patch.object(events, "MAX_FILE", 1),
            patch.object(events, "BACKUPS", 2),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for _ in range(6):
                events.event("공지", "보관 검사")
            self.assertEqual(
                len(list((Path(temp) / ".runtime/telemetry").glob("events.jsonl*"))), 3
            )

    def test_message_source_url_stays_visible_and_email_changes_only_footer_label(self):
        url = "https://example.invalid/view?id=1&mode=view"
        self.assertEqual(
            message_content("제목", url, "학과"),
            f"✔ [학과] 새로운 공지가 등록되었습니다.\n제목: 제목\n{url}",
        )
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "together.png").write_bytes(b"banner")
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(
                email_content("A < B & 「교육」", url, "<p>본문</p>", "학과", temp),
                "html.parser",
            )
            links = soup.find_all("a")
            self.assertEqual(links[0]["href"], url)
            self.assertEqual(links[0].get_text(), url)
            self.assertEqual(links[-1].get_text(), "투게더 바로가기")
            self.assertIn("A < B & 「교육」", soup.get_text())
