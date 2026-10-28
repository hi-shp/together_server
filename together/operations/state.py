"""실행 상태와 수집 결과를 운영 점검용으로 남긴다."""

from datetime import datetime
import json, os, time
from pathlib import Path

_boards = {"checked": 0, "failed": 0}


def record_board_result(ok):
    _boards["checked"] += 1
    if ok is None:
        _boards["unavailable"] = _boards.get("unavailable", 0) + 1
    else:
        _boards["failed"] += int(not ok)


def board_summary():
    return dict(_boards)


def update_state(base_dir, **fields):
    root = Path(base_dir) / ".runtime/health"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "runner.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    state.update(fields, updated_at=time.time())
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return state


def read_state(base_dir):
    path = Path(base_dir) / ".runtime/health/runner.json"
    return json.loads(path.read_text()) if path.exists() else {}
