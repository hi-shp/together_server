"""기존 전송 양식과 출력 문구, 터미널의 실시간 표시를 확인한다."""

import contextlib
from datetime import datetime
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault(
    "together.integrations.openai_client",
    types.SimpleNamespace(answer_gpt=MagicMock(), check_title_similarity=MagicMock()),
)
import run_main
from together.plato.content import email_content
from together.plato.service import WriteNoticeService
from together import config


class LegacyPresentationTests(unittest.TestCase):
    def supervisor_fixture(self):
        return json.loads(
            (Path(__file__).parent / "fixtures/legacy-supervisor.json").read_text(
                encoding="utf-8"
            )
        )

    def test_cycle_end_precedes_upload_and_log_caption_matches_original(self):
        fixture = self.supervisor_fixture()

        class Afternoon(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 10, 27, 14, 30, 0)

        output = io.StringIO()

        def check_upload(content, **kwargs):
            self.assertIn("사이클 종료", output.getvalue())
            name = "log_2026-10-27_14-30-00.txt"
            self.assertEqual(
                content,
                fixture["log_caption_prefix"] + name + fixture["log_caption_suffix"],
            )
            self.assertEqual(kwargs["channel"], "DISCORD_LOG_WEBHOOK_URL")
            self.assertEqual(kwargs["log_path"].name, name)
            self.assertEqual(kwargs["error_prefix"], fixture["log_error_prefix"])

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(run_main, "BASE_DIR", Path(temp)),
            patch.object(run_main.os, "chdir"),
            patch.object(run_main, "datetime", Afternoon),
            patch.object(run_main, "read_state", return_value={}),
            patch.object(run_main, "update_state"),
            patch.object(run_main, "cleanup_old_logs"),
            patch.object(run_main, "run_cycle", return_value=0) as cycle,
            patch.object(run_main, "notify", side_effect=check_upload) as notify,
            patch.object(run_main.time, "sleep", side_effect=StopIteration),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(StopIteration):
                run_main.run_main()
            cycle.assert_called_once()
            notify.assert_called_once()

    def test_night_pause_keeps_original_console_and_separator(self):
        fixture = self.supervisor_fixture()

        class Night(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 10, 27, 1, 0, 0)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(run_main, "BASE_DIR", Path(temp)),
            patch.object(run_main.os, "chdir"),
            patch.object(run_main, "datetime", Night),
            patch.object(run_main, "read_state", return_value={}),
            patch.object(run_main, "update_state"),
            patch.object(run_main, "cleanup_old_logs"),
            patch.object(run_main, "run_cycle") as cycle,
            patch.object(run_main, "notify") as notify,
            patch.object(run_main.time, "sleep", side_effect=StopIteration),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaises(StopIteration):
                run_main.run_main()
            self.assertEqual(output.getvalue(), fixture["night_console"] + "\n")
            notify.assert_called_once_with(
                fixture["night_separator"], channel="DISCORD_NIGHT_WEBHOOK_URL"
            )
            cycle.assert_not_called()

    def test_log_upload_failure_keeps_original_prefix_and_hides_secret(self):
        fixture = self.supervisor_fixture()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"TEST_WEBHOOK": "https://example.invalid/hook"}),
            patch.object(
                config.requests,
                "post",
                side_effect=config.requests.ConnectionError("private credential"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            path = Path(temp) / "log.txt"
            path.write_text("log", encoding="utf-8")
            self.assertFalse(
                config.notify(
                    "`log.txt`",
                    channel="TEST_WEBHOOK",
                    log_path=path,
                    error_prefix=fixture["log_error_prefix"],
                )
            )
            self.assertEqual(
                output.getvalue(), fixture["log_error_prefix"] + "ConnectionError\n"
            )
            self.assertNotIn("private credential", output.getvalue())

    def test_email_html_matches_the_original_template(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "together.png").write_bytes(b"fixture-banner")
            actual = email_content(
                "검증용 공지",
                "https://example.invalid/notice/1",
                "<p>본문과 <strong>첨부 안내</strong></p>",
                "시험학과",
                temp,
            )
        expected = (Path(__file__).parent / "fixtures/legacy-email.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            actual,
            expected.replace(
                ">https://dev-plato.pusan.ac.kr/course/view.php?id=157301</a>",
                ">투게더 바로가기</a>",
            ),
        )

    def test_console_discord_and_message_text_keep_the_original_wording(self):
        service = WriteNoticeService.__new__(WriteNoticeService)
        service._recipients = MagicMock(return_value=[{"name": "학생"}])
        client = MagicMock()
        client.send.return_value = {"sent": 199, "deduplicated": 0, "missing": 0}
        service._notification = MagicMock(return_value=client)
        with (
            patch.dict(os.environ, {"PLATO_NOTIFICATION_TRANSPORT": "http"}),
            patch("together.plato.service.notify") as notify,
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                service.send_message("공지", "https://example.invalid", "시험학과")
            self.assertIn("전송 확인 199명", output.getvalue())
            notify.assert_called_once_with(
                "시험학과/Plato 쪽지 전송 완료 (199명) -공지",
                channel="DISCORD_MESSAGE_WEBHOOK_URL",
            )
            self.assertEqual(
                client.send.call_args.args[3],
                "✔ [시험학과] 새로운 공지가 등록되었습니다.\n제목: 공지\nhttps://example.invalid",
            )
            notify.reset_mock()
            output = io.StringIO()
            with (
                patch(
                    "together.plato.service.email_content", return_value="legacy-body"
                ),
                contextlib.redirect_stdout(output),
            ):
                service.send_email(
                    "공지", "https://example.invalid", "본문", "시험학과"
                )
            self.assertIn("발송 확인 199명", output.getvalue())
            notify.assert_called_once_with(
                "시험학과/이메일 전송 완료 (199명) -공지",
                channel="DISCORD_EMAIL_WEBHOOK_URL",
            )
            self.assertEqual(
                client.send.call_args.args[2:4], ("✔ [시험학과] 공지", "legacy-body")
            )

    def test_console_and_log_receive_child_output_before_the_cycle_ends(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / ".runtime"
            runtime.mkdir()
            log = root / "log.txt"
            first = "모듈 불러오기 완료\n"
            second = "PLATO 로그인 완료\n"
            (root / "main.py").write_text(
                "import time\nprint('모듈 불러오기 완료',flush=True)\ntime.sleep(.2)\nprint('PLATO 로그인 완료',flush=True)\n",
                encoding="utf-8",
            )
            capture = io.BytesIO()
            test = self

            class Sink:
                def write(self, data):
                    if second.encode() in data:
                        test.assertIn(first.encode(), log.read_bytes())
                    return capture.write(data)

                def flush(self):
                    pass

            with (
                patch.object(run_main, "BASE_DIR", root),
                patch.object(run_main, "scratch_root", return_value=runtime),
                patch.object(
                    run_main.sys, "stdout", types.SimpleNamespace(buffer=Sink())
                ),
            ):
                self.assertEqual(run_main.run_cycle(log, max_seconds=10), 0)
            self.assertEqual(capture.getvalue(), (first + second).encode())
            self.assertEqual(log.read_bytes(), capture.getvalue())


if __name__ == "__main__":
    unittest.main()
