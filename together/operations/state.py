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


def record_detail_failure():
    # 목록 검사는 이미 checked에 포함됐다. 상세 실패로 검사 수를 늘리지 않는다.
    _boards['failed'] += 1
    _boards['detail_failed'] = _boards.get('detail_failed', 0) + 1


def record_board_deferred():
    # 응답 불가는 정상 빈 목록도 오류도 아니다. 다음 사이클에서 다시 확인한다.
    _boards['checked'] += 1
    _boards['deferred'] = _boards.get('deferred', 0) + 1


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
