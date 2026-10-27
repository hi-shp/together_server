"""환경설정과 Discord 알림."""

import os
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv
import requests

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env", override=False)
HTTP_TIMEOUT = (5, 30)
_warned = set()
notice_category = ContextVar("discord_notice_category", default=None)


def _discord_result(channel, outcome, status=None, message_id=None):
    try:
        from together.operations.events import event

        event(
            "Discord",
            "로그 전송 결과",
            console=False,
            channel=channel,
            outcome=outcome,
            status=status,
            message_id=message_id,
        )
    except Exception:
        pass


def notify(content=None, *, channel="WEBHOOK_URL", log_path=None, error_prefix=None):
    """Discord 알림을 보낸다. 중복 전송을 막기 위해 실패 시 재시도하지 않는다."""
    if (
        channel in ("DISCORD_MESSAGE_WEBHOOK_URL", "DISCORD_EMAIL_WEBHOOK_URL")
        and notice_category.get() == "해당없음"
    ):
        _discord_result(channel, "category_filtered")
        return False
    url = os.getenv(channel, "").strip()
    if not url:
        if channel not in _warned:
            print(f"Discord disabled: {channel} is unset", flush=True)
            _warned.add(channel)
        return False
    response = None
    try:
        if log_path is None:
            response = requests.post(
                url,
                json={"content": (content or "")[:2000]},
                params={"wait": "true"},
                timeout=HTTP_TIMEOUT,
            )
        else:
            with open(log_path, "rb") as handle:
                response = requests.post(
                    url,
                    data={"content": content or Path(log_path).name},
                    files={"file": (Path(log_path).name, handle, "text/plain")},
                    params={"wait": "true"},
                    timeout=HTTP_TIMEOUT,
                )
        response.raise_for_status()
        try:
            message_id = response.json().get("id")
        except (ValueError, AttributeError):
            message_id = None
        _discord_result(
            channel,
            "sent",
            response.status_code,
            message_id if isinstance(message_id, str) else None,
        )
        return True
    except (requests.RequestException, OSError) as exc:
        _discord_result(
            channel, type(exc).__name__, getattr(response, "status_code", None)
        )
        prefix = error_prefix if error_prefix is not None else f"Discord {channel}: "
        print(f"{prefix}{type(exc).__name__}", flush=True)
        return False
    finally:
        if response is not None:
            response.close()
