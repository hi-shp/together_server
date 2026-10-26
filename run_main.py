"""Single-instance supervisor with bounded logs and per-cycle process cleanup."""
import fcntl
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

from runtime_config import BASE_DIR, notify
from browser_runtime import scratch_root

MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_TOTAL_LOG_BYTES = 128 * 1024 * 1024
LOG_NAME = re.compile(r'log_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.txt')


def cleanup_old_logs(log_dir):
    cutoff = time.time() - 7 * 86400
    files = sorted((p for p in Path(log_dir).iterdir()
                    if LOG_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for path in files:
        info = path.stat()
        if info.st_mtime < cutoff or total + info.st_size > MAX_TOTAL_LOG_BYTES:
            path.unlink()
        else:
            total += info.st_size


def stop_process_group(process):
    # Called only for a child started with start_new_session=True.
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


def run_cycle(log_path, max_seconds=7200):
    runtime = scratch_root()
    workspace = Path(tempfile.mkdtemp(prefix='run-', dir=runtime))
    env = os.environ.copy()
    env.update({key: str(workspace) for key in ('TMPDIR', 'TMP', 'TEMP', 'TOGETHER_RUN_DIR')})
    process = None
    try:
        process = subprocess.Popen([sys.executable, '-u', str(BASE_DIR / 'main.py')],
                                   cwd=BASE_DIR, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        written = 0
        with open(log_path, 'wb') as logfile, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - started > max_seconds:
                    logfile.write(b'\n[supervisor] cycle timeout\n')
                    return 124
                for key, _ in selector.select(timeout=1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    available = MAX_LOG_BYTES - written
                    if available > 0:
                        logfile.write(chunk[:available])
                        written += min(available, len(chunk))
            return process.wait(timeout=10)
    finally:
        if process is not None:
            stop_process_group(process)
            process.stdout.close()
        shutil.rmtree(workspace)


def run_main():
    os.chdir(BASE_DIR)
    runtime = BASE_DIR / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    log_dir = BASE_DIR / 'logs'
    log_dir.mkdir(exist_ok=True)
    with open(runtime / 'runner.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Crawler supervisor is already running')
        failures = 0
        while True:
            cleanup_old_logs(log_dir)
            now = datetime.now()
            if now.hour < 6:
                wake = now.replace(hour=6, minute=0, second=0, microsecond=0)
                notify('-' * 100, channel='DISCORD_NIGHT_WEBHOOK_URL')
                time.sleep((wake - now).total_seconds())
                continue
            log_path = log_dir / f'log_{now:%Y-%m-%d_%H-%M-%S}.txt'
            code = run_cycle(log_path)
            notify(log_path.name, channel='DISCORD_LOG_WEBHOOK_URL', log_path=log_path)
            cleanup_old_logs(log_dir)
            failures = min(failures + 1, 5) if code else 0
            delay = min(300, 10 * 2 ** failures)
            print(f'Crawler exit={code}; next cycle in {delay}s', flush=True)
            time.sleep(delay)


def _terminate(signum, frame):
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _terminate)
    run_main()
