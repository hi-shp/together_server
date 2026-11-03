"""학교 사이트의 연결을 재사용하고 같은 서버의 연속 조회 간격을 둔다."""

from functools import lru_cache
import socket
import threading
import time
from urllib.parse import urlsplit
import requests
import atexit

_session = requests.Session()
atexit.register(_session.close)
_next_request = {}
_lock = threading.Lock()
INTERVAL = 0.0


@lru_cache(maxsize=256)
def origin_group(host):
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def get(url, **kwargs):
    group = origin_group(urlsplit(url).hostname or "")
    with _lock:
        now = time.monotonic()
        ready = max(now, _next_request.get(group, now))
        _next_request[group] = ready + INTERVAL
    if ready > now:
        time.sleep(ready - now)
    return _session.get(url, **kwargs)


def get_fresh(url, **kwargs):
    """목록 껍데기 응답 재확인에만 쿠키·연결을 분리한다."""
    with requests.Session() as session:
        return session.get(url, **kwargs)
