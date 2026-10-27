"""서버의 주요 장애를 점검하고 이메일 감시용 결과를 저장한다."""

from datetime import datetime, timezone
import argparse, hashlib, json, os
from pathlib import Path
import shutil, sqlite3, subprocess, sys, time
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.config import BASE_DIR, notify
from together.operations.state import read_state
from together.storage.attachments import AttachmentCache, GIB
from together.storage.api_usage import ApiAccounting


def evaluate(metrics, previous=None):
    previous = previous or {}
    issues = []

    def add(code, detail):
        issues.append({"code": code, "detail": detail})

    if metrics["free_bytes"] < 2 * GIB:
        add("disk_critical", "디스크 여유 공간이 2GB 아래입니다.")
    running = metrics.get("crawler_running", metrics.get("service") == "active")
    if metrics.get("expected_running") and not running:
        add("crawler_stopped", "운영하도록 설정한 크롤러가 중지됐습니다.")
    runner = metrics.get("runner", {})
    if runner.get("consecutive_failures", 0) >= 3:
        add("repeated_cycle_failure", "크롤러 실행이 3회 이상 연속 실패했습니다.")
    planned_pause = (
        runner.get("status") == "night_pause"
        and time.time() < runner.get("next_wake", 0) + 300
    )
    if (
        running
        and metrics.get("expected_running")
        and not planned_pause
        and time.time() - runner.get("updated_at", time.time()) > 3 * 3600
    ):
        add("crawler_stalled", "운영 상태 갱신이 3시간 이상 멈췄습니다.")
    memory_checks = (
        previous.get("memory_checks", 0) + 1
        if metrics.get("memory_percent", 0) >= 95
        else 0
    )
    if memory_checks >= 3:
        add("memory_critical", "메모리 사용률 95% 이상이 반복되고 있습니다.")
    boards = runner.get("boards", {})
    if (
        boards.get("checked", 0) >= 20
        and boards.get("failed", 0) / boards["checked"] >= 0.5
    ):
        add("source_outage", "점검한 게시판의 절반 이상에서 수집 오류가 발생했습니다.")
    if metrics.get("unknown_delivery_recipients", 0) >= 10:
        add(
            "notification_outcome_unknown",
            "다수 수신자의 전송 결과를 확인할 수 없습니다. 중복 발송 방지를 위해 재전송을 보류했습니다.",
        )
    if metrics.get("recent_quarantined", 0) >= 5:
        add(
            "post_quality_outbreak",
            "최근 1시간 동안 의심 공지 또는 게시 검증 오류가 5건 이상 발생했습니다.",
        )
    if metrics.get("unverified_publications", 0):
        add(
            "post_validation_failed",
            "게시된 공지 중 정상 여부를 확인할 글이 있습니다. 반복 게시와 알림 전송을 보류했습니다.",
        )
    if metrics.get("recipient_directory_mismatch"):
        add(
            "recipient_directory_mismatch",
            "알림 신청자 다수를 PLATO 수신자 목록과 대조할 수 없습니다.",
        )
    if metrics.get("email_queue_blocked", 0) >= 10:
        add("email_delivery_blocked", "완료 여부를 확인할 이메일 발송 요청이 있습니다.")
    if (
        metrics.get("email_queue_pending", 0)
        and metrics.get(
            "email_queue_idle_seconds", metrics.get("email_queue_oldest_seconds", 0)
        )
        > 1800
    ):
        add("email_queue_stalled", "이메일 발송 진행이 30분 이상 확인되지 않습니다.")
    if (
        metrics.get("email_queue_pending", 0)
        and metrics.get("email_worker") != "active"
    ):
        add(
            "email_worker_stopped",
            "대기 중인 이메일을 처리할 발송 작업자가 중지됐습니다.",
        )
    errors = metrics.get("api", {}).get("errors_last_day", {})
    for code in (
        "insufficient_quota",
        "invalid_api_key",
        "model_not_found",
        "AuthenticationError",
        "DailyApiBudgetExceeded",
    ):
        if errors.get(code, 0):
            add("api_" + code, "API 인증·잔액·모델 설정 확인이 필요합니다: " + code)
    budget = metrics.get("budget", {})
    if budget.get("balance_source") == "key_changed":
        add(
            "api_account_changed",
            "API 키가 바뀌어 보관한 계정 잔액을 적용할 수 없습니다. 새 계정 정보를 확인해 주세요.",
        )
    if (
        budget.get("estimated_remaining_usd") is not None
        and budget["estimated_remaining_usd"] < 0.25
    ):
        add(
            "api_balance_low",
            "기록된 API 사용량을 반영한 잔액 추정치가 $0.25 아래입니다. 실제 잔액 확인이 필요합니다.",
        )
    if budget.get("forecast_days") is not None and budget["forecast_days"] < 7:
        add(
            "api_runout_soon",
            "최근 실제 운영 사용량 기준으로 API 잔액 추정 소진 시점이 7일 이내입니다.",
        )
    if budget.get("expires_in_days") is not None and budget["expires_in_days"] <= 7:
        add("api_credit_expiry", "확인해 둔 API 크레딧 만료일이 7일 이내입니다.")
    return issues, {"memory_checks": memory_checks}


def budget_status(base_dir, api):
    path = Path(base_dir) / ".runtime/health/api-budget.json"
    if not path.exists():
        return {"balance_source": "unavailable"}
    value = json.loads(path.read_text())
    if (
        value.get("key_fingerprint")
        != hashlib.sha256(os.getenv("GPT_API_KEY", "").encode()).hexdigest()
    ):
        return {"balance_source": "key_changed"}
    snapshot = datetime.fromisoformat(value["observed_at"]).timestamp()
    total = 0
    accounting = ApiAccounting(base_dir)
    with accounting.connection() as db:
        total = db.execute(
            "SELECT COALESCE(SUM(cost),0) FROM usage WHERE at>=?", (snapshot,)
        ).fetchone()[0]
        # 충분한 운영 표본이 생긴 뒤에만 소진 속도를 계산한다.
        production = db.execute(
            "SELECT day,SUM(cost) FROM usage WHERE at>? AND task LIKE 'crawler:%' GROUP BY day",
            (time.time() - 7 * 86400,),
        ).fetchall()
    remaining = max(0, value["balance_usd"] - total)
    daily = (
        sum(row[1] or 0 for row in production) / len(production)
        if len(production) >= 3
        else None
    )
    expires = datetime.fromisoformat(value["credit_expires_at"]).timestamp()
    return {
        "balance_source": "dashboard_snapshot_minus_tracked_estimate",
        "snapshot_usd": value["balance_usd"],
        "snapshot_at": value["observed_at"],
        "estimated_remaining_usd": round(remaining, 2),
        "other_account_usage_included": False,
        "daily_production_estimate_usd": daily,
        "forecast_days": remaining / daily if daily else None,
        "expires_in_days": (expires - time.time()) / 86400,
        "key_expiry": value.get("key_expires_at"),
        "credit_expires_at": value["credit_expires_at"],
    }


def delivery_metrics(base_dir, now=None):
    now = time.time() if now is None else now
    unknown = 0
    ledger = base_dir / ".runtime/notification-deliveries.sqlite3"
    if ledger.exists():
        with sqlite3.connect(ledger) as db:
            unknown = db.execute(
                "SELECT COALESCE(SUM(recipients),0) FROM deliveries WHERE (state='unknown' AND updated<?) OR (state='sending' AND updated<?)",
                (now - 300, now - 900),
            ).fetchone()[0]
    queue_pending = queue_blocked = 0
    queue_oldest = queue_idle = 0
    outbox = base_dir / ".runtime/email-outbox.sqlite3"
    if outbox.exists():
        with sqlite3.connect(outbox) as db:
            queue_pending, oldest = db.execute(
                "SELECT COUNT(*),MIN(created) FROM jobs WHERE state!='sent'"
            ).fetchone()
            queue_blocked = db.execute(
                "SELECT COALESCE(SUM(recipients),0) FROM jobs WHERE state IN ('blocked','unknown') AND created<?",
                (now - 120,),
            ).fetchone()[0]
            queue_oldest = now - oldest if oldest else 0
            progress = db.execute(
                "SELECT MAX(updated) FROM jobs WHERE state IN ('sent','sending')"
            ).fetchone()[0]
            if oldest:
                queue_idle = max(0, now - max(oldest, progress or oldest))
    return {
        "unknown_delivery_recipients": unknown,
        "email_queue_pending": queue_pending,
        "email_queue_blocked": queue_blocked,
        "email_queue_oldest_seconds": queue_oldest,
        "email_queue_idle_seconds": queue_idle,
    }


def collect(base_dir):
    base_dir = Path(base_dir)
    service = subprocess.run(
        ["systemctl", "is-active", "together-crawler.service"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    running = service == "active"
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            if (
                "python" in (process.info["name"] or "").lower()
                and any(
                    Path(arg).name in ("run_main.py", "main.py")
                    for arg in process.info["cmdline"] or []
                )
                and Path(process.cwd()).resolve() == base_dir.resolve()
            ):
                running = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    runner = read_state(base_dir)
    memory = psutil.virtual_memory()
    free = shutil.disk_usage(base_dir).free
    api = ApiAccounting(base_dir).summary()
    quarantined = sum(
        path.stat().st_mtime > time.time() - 3600
        for path in (base_dir / ".runtime/quarantine").glob("*.json")
    )
    receipts = base_dir / ".runtime/published-receipts.json"
    unverified = 0
    if receipts.exists():
        unverified = sum(
            row.get("state") != "verified"
            and time.time() - row.get("updated_at", time.time()) > 120
            for row in json.loads(receipts.read_text()).values()
        )
    mismatch = False
    for path in (base_dir / ".runtime").glob("notification-missing-*.json"):
        row = json.loads(path.read_text())
        if (
            time.time() - row["updated"] < 86400
            and len(row["missing_hashes"]) >= 10
            and len(row["missing_hashes"]) / max(1, row["requested"]) >= 0.25
        ):
            mismatch = True
    worker = subprocess.run(
        ["systemctl", "is-active", "together-email.service"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "crawler_running": running,
        **delivery_metrics(base_dir),
        "email_worker": worker,
        "expected_running": os.getenv("OPERATIONS_EXPECT_CRAWLER", "0") == "1"
        or runner.get("expected_running", False),
        "runner": runner,
        "free_bytes": free,
        "memory_percent": memory.percent,
        "attachment_cache": AttachmentCache(base_dir).maintain(),
        "api": api,
        "budget": budget_status(base_dir, api),
        "recent_quarantined": quarantined,
        "unverified_publications": unverified,
        "recipient_directory_mismatch": mismatch,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = BASE_DIR / ".runtime/health"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_path = root / "previous.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    metrics = collect(BASE_DIR)
    issues, state = evaluate(metrics, previous)
    fingerprint = hashlib.sha256(
        json.dumps(sorted(item["code"] for item in issues)).encode()
    ).hexdigest()
    result = {
        "checked_at": metrics["checked_at"],
        "critical": issues,
        "fingerprint": fingerprint,
        "email_to": os.getenv("ALERT_EMAIL_TO", ""),
        "service": metrics["service"],
        "expected_running": metrics["expected_running"],
        "budget": metrics["budget"],
        "free_gib": round(metrics["free_bytes"] / GIB, 2),
        "api": metrics["api"],
        "attachment_cache": metrics["attachment_cache"],
    }
    result["email_queue"] = {
        key: metrics[key]
        for key in (
            "email_queue_pending",
            "email_queue_blocked",
            "email_queue_oldest_seconds",
            "email_queue_idle_seconds",
            "email_worker",
        )
    }
    path = root / "status.json"
    pending = path.with_suffix(".pending")
    pending.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(pending, 0o600)
    os.replace(pending, path)
    notified = previous.get("notified_codes", {})
    fresh = [
        item for item in issues if time.time() - notified.get(item["code"], 0) > 86400
    ]
    if fresh and notify(
        "투게더 운영 점검: " + "; ".join(item["detail"] for item in fresh)
    ):
        for item in fresh:
            notified[item["code"]] = time.time()
    state["notified_codes"] = {
        code: at for code, at in notified.items() if time.time() - at < 7 * 86400
    }
    previous_path.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(previous_path, 0o600)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
