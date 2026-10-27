"""기존 전송 양식과 출력 문구, 터미널의 실시간 표시를 확인한다."""

import contextlib
import io
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


class LegacyPresentationTests(unittest.TestCase):
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
        self.assertEqual(actual, expected)

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
            self.assertEqual(
                output.getvalue(), "시험학과/Plato 쪽지 전송 완료 (199명)\n"
            )
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
            self.assertEqual(output.getvalue(), "시험학과/이메일 전송 완료 (199명)\n")
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
