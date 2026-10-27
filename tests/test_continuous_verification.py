from datetime import datetime
import io, json, tempfile, types, unittest, os
from pathlib import Path
from unittest.mock import patch
from together import supervisor


class StopFixture(Exception):
    pass


class ContinuousVerification(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()

    def tearDown(self):
        os.chdir(self.cwd)

    def test_only_explicit_true_enables_continuous_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / ".runtime"
            folder.mkdir()
            self.assertFalse(supervisor.verification_mode(root))
            for value, wanted in [
                ({"continuous": True}, True),
                ({"continuous": "true"}, False),
                ({}, False),
            ]:
                (folder / "verification-mode.json").write_text(json.dumps(value))
                self.assertEqual(supervisor.verification_mode(root), wanted)

    def test_review_run_at_night_has_no_cycle_watchdog_or_night_sleep(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / ".runtime"
            folder.mkdir()
            (folder / "verification-mode.json").write_text('{"continuous":true}')
            with (
                patch.object(supervisor, "BASE_DIR", root),
                patch.object(
                    supervisor,
                    "datetime",
                    types.SimpleNamespace(now=lambda: datetime(2026, 10, 28, 3)),
                ),
                patch.object(supervisor, "run_cycle", side_effect=StopFixture) as run,
                patch.object(
                    supervisor.time,
                    "sleep",
                    side_effect=AssertionError("Unexpected night sleep"),
                ),
            ):
                with self.assertRaises(StopFixture):
                    supervisor.run_main()
            self.assertEqual(run.call_args.kwargs["idle_seconds"], 0)

    def test_original_night_pause_remains_after_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(supervisor, "BASE_DIR", root),
                patch.object(
                    supervisor,
                    "datetime",
                    types.SimpleNamespace(now=lambda: datetime(2026, 10, 28, 3)),
                ),
                patch.object(supervisor, "run_cycle") as run,
                patch.object(supervisor, "notify"),
                patch.object(supervisor.time, "sleep", side_effect=StopFixture),
                patch.object(supervisor.sys, "stdout", io.StringIO()),
            ):
                with self.assertRaises(StopFixture):
                    supervisor.run_main()
            run.assert_not_called()
            state = json.loads((root / ".runtime/health/runner.json").read_text())
            self.assertEqual(state["status"], "night_pause")
