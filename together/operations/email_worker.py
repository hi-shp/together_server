"""크롤러와 독립적으로 이메일 대기열을 처리한다."""

import argparse, fcntl, time
from together.config import BASE_DIR
from together.storage.email_outbox import EmailOutbox
from together.plato.auth import login_session
from together.plato.notifications import (
    PlatoNotificationTransport,
    NotificationOutcomeUnknown,
)


def process_job(outbox, job, transport):
    payload = job["payload"]
    try:
        if job["state"] == "unknown":
            recovered = transport.recover_event(payload["event_key"])
            if recovered is False:
                outbox.defer(job, True, "awaiting_email_receipt")
                return False
            if recovered is not None:
                message = outbox.complete(job, recovered)
                print(message, flush=True)
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
        print(message, flush=True)
        return True
    except NotificationOutcomeUnknown:
        outbox.defer(job, True, "awaiting_email_receipt")
        return False
    except Exception as exc:
        outbox.defer(job, False, type(exc).__name__)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = BASE_DIR / ".runtime"
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / "email-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        outbox = EmailOutbox(BASE_DIR)
        outbox.recover_interrupted()
        transport = None
        try:
            if not args.once:
                try:
                    transport = PlatoNotificationTransport.from_session(
                        login_session(), BASE_DIR
                    )
                except Exception:
                    transport = None
            while True:
                job = outbox.claim()
                if job is None:
                    if args.once:
                        return
                    time.sleep(2)
                    continue
                try:
                    if transport is None:
                        transport = PlatoNotificationTransport.from_session(
                            login_session(), BASE_DIR
                        )
                    succeeded = process_job(outbox, job, transport)
                    if not succeeded:
                        transport.close()
                        transport = None
                except Exception as exc:
                    outbox.defer(job, False, type(exc).__name__)
                    if transport:
                        transport.close()
                        transport = None
        finally:
            if transport:
                transport.close()


if __name__ == "__main__":
    main()
