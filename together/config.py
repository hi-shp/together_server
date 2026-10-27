"""환경설정과 Discord 알림."""

import os
from pathlib import Path

from dotenv import load_dotenv
import requests

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env", override=False)
HTTP_TIMEOUT = (5, 30)
_warned = set()


def notify(content=None, *, channel="WEBHOOK_URL", log_path=None, error_prefix=None):
    """Discord 알림을 보낸다. 중복 전송을 막기 위해 실패 시 재시도하지 않는다."""
    url = os.getenv(channel, "").strip()
    if not url:
        if channel not in _warned:
            print(f"Discord disabled: {channel} is unset", flush=True)
            _warned.add(channel)
        return False
    try:
        if log_path is None:
            response = requests.post(
                url, json={"content": (content or "")[:2000]}, timeout=HTTP_TIMEOUT
            )
        else:
            with open(log_path, "rb") as handle:
                response = requests.post(
                    url,
                    data={"content": content or Path(log_path).name},
                    files={"file": (Path(log_path).name, handle, "text/plain")},
                    timeout=HTTP_TIMEOUT,
                )
        response.raise_for_status()
        response.close()
        return True
    except (requests.RequestException, OSError) as exc:
        prefix = error_prefix if error_prefix is not None else f"Discord {channel}: "
        print(f"{prefix}{type(exc).__name__}", flush=True)
        return False
