"""크롤러와 독립적으로 이메일 대기열을 처리한다."""

import argparse, fcntl, time, sqlite3
from together.config import BASE_DIR
from together.storage.email_outbox import EmailOutbox
from together.plato.auth import login_session
from together.operations.events import event, set_notice
from together.plato.notifications import (
    PlatoNotificationTransport,
    NotificationOutcomeUnknown,
    NotificationAuthenticationError,
)


def process_job(outbox, job, transport):
    payload = job["payload"]
    set_notice(
        payload.get("url", payload["event_key"]),
        payload["department"],
        payload["title"],
    )
    try:
        if job["state"] == "unknown":
            recovered = transport.recover_event(payload["event_key"])
            if recovered is False:
                outbox.defer(job, True, "awaiting_email_receipt")
                return False
            if recovered is not None:
                message = outbox.complete(job, recovered)
                event(
                    "이메일",
                    f"늦은 발송 이력 확인 {recovered}명",
                    count=recovered,
                    outcome="recovered",
                )
                return True
        result = transport.send(
            "email",
            payload["recipients"],
            payload["subject"],
            payload["body"],
            payload["event_key"],
            test_only_id=payload.get("test_only_id"),
            expected_ids=payload.get("expected_ids"),
        )
        count = result["sent"] + result["deduplicated"]
        message = outbox.complete(job, count)
        event("이메일", f"발송 이력 확인 {count}명", count=count, outcome="verified")
        return True
    except NotificationOutcomeUnknown:
        outbox.defer(job, True, "awaiting_email_receipt")
        event(
            "이메일",
            "응답 불확실 · 발송 이력 대조 대기",
            level="WARN",
            outcome="unknown",
        )
        return False
    except Exception as exc:
        if isinstance(exc, NotificationAuthenticationError):
            transport.authentication_failed = True
        outbox.defer(job, isinstance(exc, sqlite3.Error), type(exc).__name__)
        event(
            "이메일",
            f"발송 처리 보류: {type(exc).__name__}",
            level="ERROR",
            error=type(exc).__name__,
        )
        return False


def main():
    import os
    import signal
    import threading
    from together.plato.email_dispatch import (
        prepare_snapshot,
        transport_from_snapshot,
        new_session,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("PLATO_EMAIL_CONCURRENCY", "1")),
    )
    args = parser.parse_args()
    if args.concurrency < 0:
        parser.error("concurrency must be zero or positive")
    root = BASE_DIR / ".runtime"
    root.mkdir(mode=0o700, exist_ok=True)
    stop = threading.Event()
    refresh = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with (root / "email-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        outbox = EmailOutbox(BASE_DIR)
        outbox.recover_interrupted()
        snapshot = None
        refreshed_at = 0
        active = {}

        def send(job, current):
            transport = None
            try:
                if job["state"] == "unknown":
                    transport = PlatoNotificationTransport.from_session(
                        new_session(current), BASE_DIR
                    )
                    transport.history_reader = current["history"]
                    recovered = transport.recover_event(job["payload"]["event_key"])
                    if recovered is False:
                        outbox.defer(job, True, "awaiting_email_receipt")
                        return
                    if recovered is not None:
                        outbox.complete(job, recovered)
                        set_notice(
                            job["payload"].get("url", job["payload"]["event_key"]),
                            job["payload"]["department"],
                            job["payload"]["title"],
                        )
                        event(
                            "이메일",
                            f"늦은 발송 이력 확인 {recovered}명",
                            count=recovered,
                            outcome="recovered",
                        )
                        return
                    transport.close()
                    transport = None
                transport = transport_from_snapshot(current, BASE_DIR)
                if not process_job(outbox, job, transport) and getattr(
                    transport, "authentication_failed", False
                ):
                    refresh.set()
            except Exception as exc:
                outbox.defer(job, isinstance(exc, sqlite3.Error), type(exc).__name__)
                refresh.set()
            finally:
                if transport:
                    transport.close()

        try:
            while not stop.is_set():
                active = {
                    key: thread for key, thread in active.items() if thread.is_alive()
                }
                if args.concurrency and len(active) >= args.concurrency:
                    stop.wait(0.2)
                    continue
                with outbox.connection() as db:
                    ready = db.execute(
                        "SELECT 1 FROM jobs WHERE state IN ('pending','unknown') AND ready<=? LIMIT 1",
                        (time.time(),),
                    ).fetchone()
                if not ready:
                    if args.once:
                        return
                    stop.wait(2)
                    continue
                if (
                    snapshot is None
                    or (refresh.is_set() and time.monotonic() - refreshed_at > 60)
                    or time.monotonic() - refreshed_at > 300
                ):
                    try:
                        refresh.clear()
                        snapshot = prepare_snapshot(BASE_DIR, snapshot)
                        refreshed_at = time.monotonic()
                    except Exception as exc:
                        event(
                            "이메일",
                            "발송 로그인 확인 실패",
                            level="ERROR",
                            error=type(exc).__name__,
                        )
                        if args.once:
                            raise
                        stop.wait(10)
                        continue
                job = outbox.claim(ordered=args.concurrency == 1)
                if job is None:
                    if args.once:
                        return
                    stop.wait(2)
                    continue
                if args.once:
                    send(job, snapshot)
                    return
                thread = threading.Thread(
                    target=send, args=(job, snapshot), name="email-" + job["id"][:8]
                )
                try:
                    thread.start()
                    active[job["id"]] = thread
                except RuntimeError:
                    outbox.defer(job, False, "thread_start_failed")
                    stop.wait(1)
        finally:
            # 발송 중인 요청의 응답과 기록을 마친 뒤 종료한다.
            for thread in active.values():
                thread.join()


if __name__ == "__main__":
    main()
