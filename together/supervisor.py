"""크롤러 반복 실행과 로그 관리."""

import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

from together.config import BASE_DIR, notify
from together.plato.browser_runtime import scratch_root
from together.operations.state import update_state, read_state
from together.operations.events import event

LOG_NAME = re.compile(r"log_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.txt")


def cleanup_old_logs(log_dir):
    cutoff = time.time() - 7 * 86400
    files = sorted(
        (
            p
            for p in Path(log_dir).iterdir()
            if LOG_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files:
        info = path.stat()
        if info.st_mtime < cutoff:
            path.unlink()


def stop_process_group(process):
    # 이 실행에서 만든 자식 프로세스 그룹만 종료한다.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def run_cycle(log_path, max_seconds=0, idle_seconds=0):
    runtime = scratch_root()
    workspace = Path(tempfile.mkdtemp(prefix="run-", dir=runtime))
    env = os.environ.copy()
    env.update(
        {key: str(workspace) for key in ("TMPDIR", "TMP", "TEMP", "TOGETHER_RUN_DIR")}
    )
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(BASE_DIR / "main.py")],
            cwd=BASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        started = time.monotonic()
        last_output = started
        with (
            open(log_path, "wb", buffering=0) as logfile,
            selectors.DefaultSelector() as selector,
        ):
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                if max_seconds and now - started > max_seconds:
                    logfile.write(b"\n[supervisor] cycle timeout\n")
                    return 124
                if idle_seconds and now - last_output > idle_seconds:
                    logfile.write(b"\n[supervisor] no progress for 20 minutes\n")
                    return 124
                for key, _ in selector.select(timeout=1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    last_output = time.monotonic()
                    try:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                    except BrokenPipeError:
                        pass
                    logfile.write(chunk)
            return process.wait(timeout=10)
    finally:
        if process is not None:
            stop_process_group(process)
            process.stdout.close()
        shutil.rmtree(workspace)


def verification_mode(base_dir):
    try:
        value = json.loads(
            (Path(base_dir) / ".runtime/verification-mode.json").read_text()
        )
        return value.get("continuous") is True
    except (OSError, ValueError, AttributeError):
        return False


def run_main():
    os.chdir(BASE_DIR)
    runtime = BASE_DIR / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    with open(runtime / "runner.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Crawler supervisor is already running")
        failures = read_state(BASE_DIR).get("consecutive_failures", 0)
        update_state(BASE_DIR, expected_running=True, status="starting")
        while True:
            cleanup_old_logs(log_dir)
            now = datetime.now()
            continuous = verification_mode(BASE_DIR)
            if now.hour < 6 and not continuous:
                print(
                    "현재 시간이 밤 12시 ~ 오전 6시이므로 6시간 동안 대기합니다.",
                    flush=True,
                )
                wake = now.replace(hour=6, minute=0, second=0, microsecond=0)
                notify("-" * 108, channel="DISCORD_NIGHT_WEBHOOK_URL")
                update_state(
                    BASE_DIR,
                    status="night_pause",
                    next_wake=time.time() + (wake - now).total_seconds(),
                )
                time.sleep((wake - now).total_seconds())
                continue
            log_path = log_dir / f"log_{now:%Y-%m-%d_%H-%M-%S}.txt"
            update_state(
                BASE_DIR,
                status="running",
                cycle_started=time.time(),
                next_wake=0,
                boards={"checked": 0, "failed": 0},
            )
            code = run_cycle(log_path)
            cleanup_old_logs(log_dir)
            failures = failures + 1 if code else 0
            fields = {
                "status": "waiting",
                "last_exit": code,
                "consecutive_failures": failures,
            }
            if not code:
                fields["last_success"] = time.time()
            update_state(BASE_DIR, **fields)
            delay = 10
            event(
                "반복 실행",
                f"사이클 종료 · 종료 코드 {code} · 연속 실패 {failures}회 · 10초 뒤 재실행",
                status=code,
                count=failures,
            )
            notify(
                f"`{log_path.name}`",
                channel="DISCORD_LOG_WEBHOOK_URL",
                log_path=log_path,
                error_prefix="디스코드 전송 중 예외 발생: ",
            )
            time.sleep(delay)


def _terminate(signum, frame):
    update_state(BASE_DIR, expected_running=False, status="stopped_by_signal")
    raise SystemExit(128 + signum)


def cli():
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    run_main()
