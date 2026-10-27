"""이메일 요청이 로그인과 수신자 화면을 함께 사용하도록 준비한다."""

from collections import OrderedDict
from html.parser import HTMLParser
import threading
import time
from urllib.parse import urljoin, urlsplit, parse_qs
import requests

from together.plato.auth import login_session
from together.plato.notifications import (
    BASE,
    COURSE,
    HOST,
    PlatoNotificationTransport,
    NotificationError,
    parse_email_history,
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
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.lock = threading.Lock()
        self.records = OrderedDict()
        self.expires = 0

    def __call__(self, *, fresh=False):
        with self.lock:
            if not fresh and time.monotonic() < self.expires:
                return list(self.records.values())
            known = set(self.records)
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
                    # 지난번 확인한 행까지 읽어 동시에 늘어난 발송 기록을 대조한다.
                    if not known or any(row["eid"] in known for row in rows):
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
            self.expires = time.monotonic() + 2
            return list(self.records.values())


def prepare_snapshot(base_dir):
    transport = PlatoNotificationTransport.from_session(login_session(), base_dir)
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
        snapshot["history"] = SharedEmailHistory(snapshot)
        return snapshot
    finally:
        transport.close()


def transport_from_snapshot(snapshot, base_dir):
    transport = PlatoNotificationTransport.from_session(new_session(snapshot), base_dir)
    transport.form_snapshot = snapshot["forms"]
    transport.student_ids = snapshot["student_ids"].copy()
    transport.history_reader = snapshot["history"]
    return transport
