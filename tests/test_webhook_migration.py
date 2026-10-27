import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location(
    "migration", Path(__file__).resolve().parents[1] / "ops/configure_webhooks.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def test_cli_keeps_configured_webhooks_when_legacy_files_are_gone(self):
        for revision in (None, "modern-code"):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / ".env"
                target.write_text(
                    "PLATO_PW=unchanged\n"
                    + "\n".join(f"{key}=rotated-{key}" for key in migration.MAPPINGS)
                    + "\n",
                    encoding="utf-8",
                )
                before = target.read_bytes()
                argv = ["configure_webhooks.py", "--root", temp]
                if revision:
                    argv += ["--from-ref", revision]
                with (
                    patch("sys.argv", argv),
                    patch.object(
                        migration.subprocess,
                        "check_output",
                        side_effect=AssertionError("Legacy Git read is unnecessary"),
                    ) as read,
                ):
                    migration.main()
                    read.assert_not_called()
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(list(Path(temp).glob(".env.backup.*")), [])

    def test_cli_reads_only_the_source_needed_by_a_missing_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".env"
            target.write_text(
                "PLATO_PW=unchanged\n"
                + "\n".join(
                    f"{key}=rotated-{key}"
                    for key in migration.MAPPINGS
                    if key != "DISCORD_EMAIL_WEBHOOK_URL"
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                patch(
                    "sys.argv",
                    ["configure_webhooks.py", "--root", temp, "--from-ref", "legacy"],
                ),
                patch.object(
                    migration.subprocess,
                    "check_output",
                    return_value=self.sources()["selenium_service.py"].encode("utf-8"),
                ) as read,
            ):
                migration.main()
                read.assert_called_once_with(
                    ["git", "show", "legacy:selenium_service.py"], cwd=Path(temp)
                )
            values = dotenv_values(target)
            self.assertEqual(values["DISCORD_EMAIL_WEBHOOK_URL"], "old-email")
            self.assertEqual(
                values["DISCORD_MESSAGE_WEBHOOK_URL"],
                "rotated-DISCORD_MESSAGE_WEBHOOK_URL",
            )
            self.assertEqual(values["PLATO_PW"], "unchanged")

    def sources(self):
        return {
            "run_main.py": "webhook_url='old-log'\nWEBHOOK_URL='old-night'\n",
            "selenium_service.py": "message_bot_url='old-message'\nemail_bot_url='old-email'\n",
        }

    def test_exact_values_and_other_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".env"
            target.write_text(
                "WEBHOOK_URL='original-common'\nPLATO_PW='unchanged'\n",
                encoding="utf-8",
            )
            migration.migrate(target, self.sources())
            values = dotenv_values(target)
            self.assertEqual(values["WEBHOOK_URL"], "original-common")
            self.assertEqual(values["PLATO_PW"], "unchanged")
            self.assertEqual(values["DISCORD_LOG_WEBHOOK_URL"], "old-log")
            self.assertEqual(values["DISCORD_NIGHT_WEBHOOK_URL"], "old-night")
            self.assertEqual(values["DISCORD_MESSAGE_WEBHOOK_URL"], "old-message")
            self.assertEqual(values["DISCORD_EMAIL_WEBHOOK_URL"], "old-email")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            before = target.read_bytes()
            migration.migrate(target, self.sources())
            self.assertEqual(target.read_bytes(), before)

    def test_existing_environment_values_take_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".env"
            target.write_text(
                "DISCORD_LOG_WEBHOOK_URL='configured-log'\n", encoding="utf-8"
            )
            migration.migrate(target, self.sources())
            self.assertEqual(
                dotenv_values(target)["DISCORD_LOG_WEBHOOK_URL"], "configured-log"
            )

    def test_missing_source_does_not_modify_env(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".env"
            target.write_text("PLATO_PW=unchanged\n", encoding="utf-8")
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                migration.migrate(
                    target, {"run_main.py": "", "selenium_service.py": ""}
                )
            self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
