"""Private, per-driver scratch directories instead of global /tmp deletion."""
import os
import shutil
import tempfile
from pathlib import Path
import psutil

import stat


def scratch_root():
    # Unix-domain socket paths are limited (~108 bytes). A project path can be
    # too long for Chromium's temporary SingletonSocket even with a fresh profile.
    root = Path(f'/tmp/together-{os.getuid()}')
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Unsafe browser scratch root')
    return root


def driver_children(service):
    process = getattr(service, 'process', None)
    if process is None:
        return []
    try:
        return psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        return []


def stop_children(children):
    # Process objects track creation time, protecting against PID reuse.
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(children, timeout=3)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=3)


def workspace_root():
    run_dir = os.getenv('TOGETHER_RUN_DIR')
    if run_dir:
        run_dir = Path(run_dir)
        if run_dir.parent != scratch_root() or not run_dir.name.startswith('run-') or run_dir.is_symlink():
            raise ValueError('Unexpected run directory')
        return run_dir / 'browser'
    return scratch_root()


def new_workspace():
    root = workspace_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='driver-', dir=root))


def cleanup_workspace(path):
    if path is None:
        return
    path = Path(path)
    root = workspace_root()
    if path.parent != root or not path.name.startswith('driver-') or path.is_symlink():
        raise ValueError('Refusing unexpected browser workspace')
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Do not sudo/chmod globally, and never turn successful posting into a crash.
        print(f'Browser cleanup deferred: {type(exc).__name__}', flush=True)
