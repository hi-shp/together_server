"""이메일 요청이 로그인과 수신자 화면을 함께 사용하도록 준비한다."""

from collections import OrderedDict
from html.parser import HTMLParser
import threading
import time
import sqlite3
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs
import requests

from together.plato.auth import login_session, http_login_session
from together.plato.notifications import (
    BASE,
    COURSE,
    HOST,
    PlatoNotificationTransport,
    NotificationError,
    NotificationAuthenticationError,
    parse_email_history,
    parse_send_form_hidden,
)


def new_session(snapshot):
    session = requests.Session()
    session.cookies.update(snapshot["cookies"])
    session.headers.update(snapshot["headers"])
    return session


class _HistoryLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        url = urljoin(BASE, dict(attrs).get("href", ""))
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if (
            parts.scheme == "https"
            and parts.netloc == HOST
            and parts.path == "/local/ubmessage/email_log.php"
            and query.get("id") == [COURSE]
            and query.get("page", ["0"])[0].isdigit()
        ):
            self.urls.add(url)


class SharedEmailHistory:
    def __init__(self, snapshot, base_dir=None):
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.floor = 0
        self.snapshot = snapshot
        self.lock = threading.Lock()
        self.records = OrderedDict()
        self.snapshot_records = ()
        self.initialized = False
        self.refreshed_at = 0

    def outstanding_floor(self):
        floors = []
        if self.base_dir:
            path = self.base_dir / ".runtime/notification-deliveries.sqlite3"
            with sqlite3.connect(path, timeout=5) as db:
                keys = db.execute(
                    "SELECT id FROM deliveries WHERE kind='email' AND state IN ('sending','unknown')"
                ).fetchall()
            for (key,) in keys:
                attempt = (
                    self.base_dir / ".runtime/notification-attempts" / (key + ".json")
                )
                if attempt.exists():
                    previous = json.loads(attempt.read_text())["previous_email_ids"]
                    floors.append(max((int(value) for value in previous), default=0))
        return min(floors, default=self.floor)

    def __call__(self, *, fresh=False, recent_ok=False):
        # 발송 전에는 한 번 읽은 기준 이력을 사용한다. 확인 조회의 응답을 기다리지 않는다.
        if not fresh and self.initialized:
            return list(self.snapshot_records)
        with self.lock:
            if self.initialized and (
                not fresh or (recent_ok and time.monotonic() - self.refreshed_at < 2)
            ):
                return list(self.snapshot_records)
            floor = self.outstanding_floor()
            urls = [BASE + "email_log.php?id=" + COURSE]
            visited = set()
            with new_session(self.snapshot) as session:
                while urls:
                    url = urls.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)
                    with session.get(
                        url, timeout=(5, 30), allow_redirects=False
                    ) as response:
                        if response.status_code != 200:
                            raise NotificationError("Email receipt history unavailable")
                        rows = parse_email_history(response.text)
                        links = _HistoryLinks()
                        links.feed(response.text)
                    for row in rows:
                        self.records[row["eid"]] = row
                    # 먼저 접수된 요청이 늦게 끝나도 그 요청의 기준 이력까지 읽는다.
                    if not self.initialized:
                        self.floor = max((int(row["eid"]) for row in rows), default=0)
                        break
                    if rows and all(int(row["eid"]) <= floor for row in rows):
                        break
                    urls.extend(
                        sorted(
                            links.urls - visited,
                            key=lambda value: int(
                                parse_qs(urlsplit(value).query).get("page", [0])[0]
                            ),
                        )
                    )
            self.records = OrderedDict(
                sorted(
                    self.records.items(), key=lambda item: int(item[0]), reverse=True
                )[:5000]
            )
            self.snapshot_records = tuple(self.records.values())
            self.initialized = True
            self.refreshed_at = time.monotonic()
            return list(self.snapshot_records)


def prepare_snapshot(base_dir, previous=None):
    session = new_session(previous) if previous else login_session()
    transport = PlatoNotificationTransport.from_session(session, base_dir)
    try:
        forms = {}
        for kind in ("email", "message"):
            transport.prepare(kind, [])
            forms[kind] = transport.last_form
        snapshot = {
            "cookies": transport.session.cookies.copy(),
            "headers": dict(transport.session.headers),
            "forms": forms,
            "student_ids": dict(transport.student_ids),
        }
        snapshot["history"] = SharedEmailHistory(snapshot, base_dir)
        snapshot["history"](fresh=True)
        return snapshot
    except NotificationAuthenticationError:
        if previous:
            transport.close()
            return prepare_snapshot(base_dir)
        raise
    finally:
        transport.close()


def transport_from_snapshot(snapshot, base_dir):
    # 요청마다 독립 세션을 사용해 메일 발송의 세션 잠금 대기를 줄인다.
    session = http_login_session()
    transport = None
    try:
        page = BASE + "email_send.php?id=" + COURSE + "&lang=ko"
        with session.get(page, timeout=(5, 30), allow_redirects=False) as response:
            if response.status_code != 200:
                raise NotificationError("Notification form unavailable")
            hidden = parse_send_form_hidden(response.text, "email", page)
        transport = PlatoNotificationTransport.from_session(session, base_dir)
        transport.form_snapshot = dict(snapshot["forms"])
        transport.form_snapshot["email"] = (hidden, snapshot["forms"]["email"][1])
        transport.student_ids = snapshot["student_ids"].copy()
        transport.history_reader = snapshot["history"]
        transport.background_dispatch = True
        return transport
    except BaseException:
        if transport:
            transport.close()
        else:
            session.close()
        raise
