"""터미널 진행 상황과 용량을 제한한 분석용 기록."""

from contextvars import ContextVar
from datetime import datetime
from functools import lru_cache
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from together.config import BASE_DIR

_context = ContextVar("crawler_log_context", default={})
_warned = False
MAX_FILE = 8 * 1024**2
BACKUPS = 31
RETENTION = 90 * 86400
FIELDS = set(
    "source_url title department result method category similarity candidates reason elapsed_seconds count sent deduplicated missing expected latest_number saved_number filename bytes sha256 error status model score notice_id cycle_id board_index board_count outcome attachment_count".split()
)


@lru_cache(maxsize=2)
def _revision(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = result.stdout.strip()
        return value if re.fullmatch(r"[0-9a-f]{40}", value) else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def safe_text(value):
    value = "".join(
        c for c in str(value) if unicodedata.category(c) not in ("Cc", "Cf", "Cs")
    )
    value = re.sub(
        r"https?://discord(?:app)?\.com/api/webhooks/[^\s<>\"']+",
        "[웹훅 숨김]",
        value,
        flags=re.I,
    )
    value = re.sub(r"\bsk-(?:proj-)?[\w-]{20,}", "[키 숨김]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[이메일 숨김]", value)
    value = re.sub(
        r"(?i)(token|password|secret|api.?key|sesskey|session)=([^\s&]+)",
        r"\1=[숨김]",
        value,
    )
    return value[:2000]


def safe_url(value):
    try:
        parts = urlsplit(str(value))
    except ValueError:
        return "[잘못된 주소]"
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return "[주소 숨김]"
    if re.search(r"/api/(?:v\d+/)?webhooks/", parts.path):
        return "[웹훅 숨김]"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not re.search(
            r"token|password|secret|api.?key|sesskey|session|signature", k, re.I
        )
    ]
    return safe_text(
        urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    )


def start_cycle():
    _context.set({"cycle_id": uuid.uuid4().hex[:10]})


def clear_notice():
    _context.set({k: v for k, v in _context.get().items() if k == "cycle_id"})


def set_notice(url, department="", title=""):
    context = {k: v for k, v in _context.get().items() if k == "cycle_id"}
    context.update(
        notice_id=hashlib.sha256(str(url).encode()).hexdigest()[:10],
        source_url=safe_url(url),
        department=safe_text(department),
        title=safe_text(title),
    )
    _context.set(context)


def _value(key, value):
    if key == "source_url":
        return safe_url(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [safe_text(v) for v in value[:3]]
    return safe_text(value)


def _save(record):
    root = BASE_DIR / ".runtime/telemetry"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with (root / "events.lock").open("a") as lock:
        os.chmod(root / "events.lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "events.jsonl"
        for old in root.glob("events.jsonl*"):
            if (
                old.is_file()
                and not old.is_symlink()
                and old.stat().st_mtime < time.time() - RETENTION
            ):
                old.unlink()
        if path.exists() and path.stat().st_size + len(line.encode()) > MAX_FILE:
            (root / f"events.jsonl.{BACKUPS}").unlink(missing_ok=True)
            for index in range(BACKUPS - 1, 0, -1):
                old = root / f"events.jsonl.{index}"
                if old.exists():
                    old.replace(root / f"events.jsonl.{index+1}")
            path.replace(root / "events.jsonl.1")
        with path.open("a", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            handle.write(line)


def event(stage, message, *, level="INFO", console=True, plain=False, **fields):
    global _warned
    now = datetime.now().astimezone()
    record = {
        "schema": 1,
        "revision": _revision(str(BASE_DIR)),
        "time": now.isoformat(timespec="seconds"),
        "level": level,
        "stage": safe_text(stage),
        "message": safe_text(message),
        **_context.get(),
    }
    record.update(
        {key: _value(key, value) for key, value in fields.items() if key in FIELDS}
    )
    if console and (
        level != "DEBUG" or os.getenv("CRAWLER_LOG_LEVEL", "INFO") == "DEBUG"
    ):
        identity = record.get("notice_id") or record.get("cycle_id") or "운영"
        print(
            (
                record["message"]
                if plain
                else f"{now:%H:%M:%S} {level:5} [{identity}] {stage} | {record['message']}"
            ),
            flush=True,
        )
    try:
        _save(record)
    except (OSError, ValueError) as exc:
        if not _warned:
            print(
                f"{now:%H:%M:%S} WARN  [운영] 기록 저장 생략 | {type(exc).__name__}",
                flush=True,
            )
            _warned = True


duplicate_review = ContextVar("duplicate_review", default="")
