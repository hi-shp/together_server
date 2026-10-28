from datetime import datetime
import io, json, tempfile, types, unittest, os
from pathlib import Path
from unittest.mock import patch
from together import supervisor
from together.operations.cycle_review import record_failed_cycle


class StopFixture(Exception):
    pass


class ContinuousVerification(unittest.TestCase):
    def test_failure_monitor_records_retry_without_stopping_runner(self):
        state = {"successful_cycles": [{"finished_at": 1}], "failed_cycles": []}
        runner = {"status": "waiting", "last_exit": 1, "updated_at": 2}
        self.assertTrue(record_failed_cycle(state, runner, 3))
        self.assertEqual(state["status"], "monitoring")
        self.assertEqual(state["successful_cycles"], [])
        self.assertEqual(state["interrupted_successful_cycles"], [{"finished_at": 1}])
        self.assertFalse(record_failed_cycle(state, runner, 4))
        self.assertEqual(len(state["failed_cycles"]), 1)

    def test_supervisor_restarts_after_repeated_failed_children(self):
        class EndTest(BaseException):
            pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / ".runtime"
            folder.mkdir()
            (folder / "verification-mode.json").write_text('{"continuous":true}')
            with (
                patch.object(supervisor, "BASE_DIR", root),
                patch.object(supervisor, "run_cycle", side_effect=[1, 1, 0]) as run,
                patch.object(supervisor, "notify"),
                patch.object(supervisor, "read_state", return_value={"consecutive_failures": 5}),
                patch.object(supervisor.time, "sleep", side_effect=[None, None, EndTest()]),
            ):
                with self.assertRaises(EndTest):
                    supervisor.run_main()
            self.assertEqual(run.call_count, 3)
            state = json.loads((folder / "health/runner.json").read_text())
            self.assertEqual(state["last_exit"], 0)
            self.assertEqual(state["consecutive_failures"], 0)
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
            self.assertEqual(run.call_args.kwargs.get("idle_seconds", 0), 0)

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
