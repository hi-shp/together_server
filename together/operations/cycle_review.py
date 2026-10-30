"""실행을 중단하지 않고 연속 사이클 검증 결과를 기록한다."""


def record_failed_cycle(state, runner, now):
    if (
        runner.get("status") != "waiting"
        or not runner.get("last_exit")
        or runner.get("updated_at") == state.get("last_completed_update")
    ):
        return False
    state["last_completed_update"] = runner["updated_at"]
    state.setdefault("failed_cycles", []).append(
        {"time": now, "exit_code": runner["last_exit"]}
    )
    state["failed_cycles"] = state["failed_cycles"][-100:]
    if state.get("successful_cycles"):
        state.setdefault("interrupted_successful_cycles", []).extend(state["successful_cycles"])
        state["interrupted_successful_cycles"] = state["interrupted_successful_cycles"][-20:]
    state["successful_cycles"] = []
    state["status"] = "monitoring"
    return True


def record_complete_cycle(state, cycle, expected_boards):
    """종료 코드와 실제 게시판 확인 범위가 모두 맞을 때만 연속 성공으로 센다."""
    boards = cycle.get("boards", {})
    if cycle.get("exit_code") != 0 or boards.get("checked") != expected_boards or boards.get("failed", 0):
        state.setdefault("incomplete_cycles", []).append(cycle)
        state["incomplete_cycles"] = state["incomplete_cycles"][-100:]
        if state.get("successful_cycles"):
            state.setdefault("interrupted_successful_cycles", []).extend(state["successful_cycles"])
            state["interrupted_successful_cycles"] = state["interrupted_successful_cycles"][-20:]
        state["successful_cycles"] = []
        state["status"] = "monitoring"
        return False
    state.setdefault("successful_cycles", []).append(cycle)
    return True
