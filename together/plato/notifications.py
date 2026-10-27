"""PLATO 전송 양식을 읽어 검증된 수신자에게 한 번씩 발송한다."""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import signal
import threading
from html.parser import HTMLParser
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup
import requests

HOST = "dev-plato.pusan.ac.kr"
COURSE = "157301"
BASE = f"https://{HOST}/local/ubmessage/"


class NotificationError(RuntimeError):
    pass


class NotificationOutcomeUnknown(NotificationError):
    pass


def normalize(value):
    return " ".join(str(value).split())


@contextmanager
def request_deadline(seconds):
    if threading.current_thread() is not threading.main_thread():
        # 각 요청의 연결·응답 제한 시간은 스레드에서도 적용된다.
        yield
        return

    def expired(signum, frame):
        raise TimeoutError("Notification deadline exceeded")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class _SendFormParser(HTMLParser):
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.forms = []
        self.current = None
        self.inputs = []
        self.labels = {}
        self.label = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = None
            if (
                urljoin(self.page_url, attrs.get("action", "")) == BASE + "action.php"
                and attrs.get("method", "").lower() == "post"
            ):
                self.current = {}
                self.forms.append(self.current)
        elif tag == "input":
            if (
                self.current is not None
                and attrs.get("type") == "hidden"
                and attrs.get("name")
            ):
                self.current[attrs["name"]] = attrs.get("value", "")
            if attrs.get("type") == "checkbox" and attrs.get("name") == "tousers[]":
                self.inputs.append(attrs)
        elif tag == "label":
            self.label = {"text": [], "title": attrs.get("title", "")}
            self.labels[attrs.get("for")] = self.label

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None
        elif tag == "label":
            self.label = None

    def handle_data(self, data):
        if self.label is not None:
            self.label["text"].append(data.strip())


def parse_send_form(html, kind, page_url):
    parser = _SendFormParser(page_url)
    parser.feed(html)
    parser.close()
    if len(parser.forms) != 1:
        raise NotificationError("Notification form unavailable")
    hidden = parser.forms[0]
    expected = "message_send_group" if kind == "message" else "email_send"
    if hidden.get("type") != expected or hidden.get("courseid") != COURSE:
        raise NotificationError("Notification form scope mismatch")
    targets = []
    for node in parser.inputs:
        value = node.get("value", "")
        if not value.isdigit():
            raise NotificationError("Invalid recipient ID")
        label = parser.labels.get(node.get("id"))
        if label:
            targets.append(
                {
                    "id": value,
                    "text": normalize(" ".join(label["text"])),
                    "title": normalize(label["title"]),
                }
            )
    return hidden, targets


def resolve_recipients(targets, recipients, kind):
    ids = []
    missing = []
    for person in recipients:
        name = normalize(person["name"])
        identity = person["student_id"] if kind == "message" else person["email"]
        wanted = normalize(f"{name} ({identity})")
        matches = [
            target["id"]
            for target in targets
            if target["title"] == wanted
            or target["text"] == wanted
            or (kind == "message" and target["text"].startswith(wanted + " "))
        ]
        if len(set(matches)) == 1:
            ids.append(matches[0])
        else:
            missing.append(hashlib.sha256(wanted.encode()).hexdigest()[:12])
    return list(dict.fromkeys(ids)), missing


class DeliveryLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(self.path, timeout=10)
        os.chmod(self.path, 0o600)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY,kind TEXT,state TEXT,updated REAL,recipients INTEGER,seconds REAL,detail TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS delivery_recipients (event TEXT,kind TEXT,uid TEXT,state TEXT,delivery_id TEXT,updated REAL,PRIMARY KEY(event,kind,uid))"
        )
        self.connection.execute(
            "DELETE FROM deliveries WHERE updated<? AND state IN ('sent','rejected')",
            (time.time() - 90 * 86400,),
        )
        self.connection.execute(
            "DELETE FROM delivery_recipients WHERE updated<? AND state='sent'",
            (time.time() - 90 * 86400,),
        )
        self.connection.commit()

    def pending_recipients(self, kind, event_key, ids):
        event = hashlib.sha256(
            json.dumps([kind, event_key], ensure_ascii=False).encode()
        ).hexdigest()
        # 이전 100명 단위 기록도 같은 수신자의 발송 이력으로 이어 쓴다.
        batches = [ids] + [
            ids[start : start + 100] for start in range(0, len(ids), 100)
        ]
        for batch in batches:
            if not batch:
                continue
            key = hashlib.sha256(
                json.dumps([kind, event_key, batch], ensure_ascii=False).encode()
            ).hexdigest()
            row = self.connection.execute(
                "SELECT state,updated FROM deliveries WHERE id=?", (key,)
            ).fetchone()
            if row:
                self.connection.executemany(
                    "INSERT OR IGNORE INTO delivery_recipients VALUES (?,?,?,?,?,?)",
                    [(event, kind, uid, row[0], key, row[1]) for uid in batch],
                )
        self.connection.commit()
        states = {
            row[0]: (row[1], row[2])
            for row in self.connection.execute(
                "SELECT uid,state,delivery_id FROM delivery_recipients WHERE event=? AND kind=?",
                (event, kind),
            )
        }
        completed = [uid for uid in ids if states.get(uid, ("",))[0] == "sent"]
        unknown = {
            states[uid][1] for uid in ids if uid in states and states[uid][0] != "sent"
        }
        pending = [uid for uid in ids if uid not in states]
        return event, pending, len(completed), unknown

    def claim(self, key, kind, count, event=None, recipient_ids=None):
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "SELECT state FROM deliveries WHERE id=?", (key,)
        ).fetchone()
        if row:
            self.connection.commit()
            return row[0]
        if event:
            existing = {
                row[0]: row[1]
                for row in self.connection.execute(
                    "SELECT uid,state FROM delivery_recipients WHERE event=? AND kind=?",
                    (event, kind),
                )
            }
            if any(uid in existing for uid in recipient_ids):
                self.connection.rollback()
                if all(existing.get(uid) == "sent" for uid in recipient_ids):
                    return "sent"
                raise NotificationOutcomeUnknown("Recipient delivery state changed")
        self.connection.execute(
            "INSERT INTO deliveries VALUES (?,?,?,?,?,?,?)",
            (key, kind, "sending", time.time(), count, 0, ""),
        )
        if event:
            self.connection.executemany(
                "INSERT INTO delivery_recipients VALUES (?,?,?,?,?,?)",
                [
                    (event, kind, uid, "sending", key, time.time())
                    for uid in recipient_ids
                ],
            )
        self.connection.commit()
        return "new"

    def state(self, key):
        row = self.connection.execute(
            "SELECT state FROM deliveries WHERE id=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def finish(self, key, state, seconds, detail=""):
        self.connection.execute(
            "UPDATE deliveries SET state=?,updated=?,seconds=?,detail=? WHERE id=?",
            (state, time.time(), seconds, detail, key),
        )
        self.connection.execute(
            "UPDATE delivery_recipients SET state=?,updated=? WHERE delivery_id=?",
            (state, time.time(), key),
        )
        self.connection.commit()

    def close(self):
        self.connection.close()


def response_is_success(response):
    # 실제 서버의 완료 응답 문구를 기준으로 전송 접수를 확인한다.
    if response.status_code not in (200, 302, 303):
        return False
    text = response.text
    return bool(
        re.search(
            r"(?:메세지|메시지|이메일|메일)(?:가|이)?\s*(?:전달|전송|발송)되었습니다|전송(?:이|을)?\s*(?:완료|되었습니다|하였습니다)|성공적으로\s*전송|발송(?:이)?\s*(?:완료|되었습니다)|successfully\s+sent",
            text,
            re.I,
        )
    )


def response_recipient_count(text):
    match = re.search(
        r"(\d+)명에게\s*(?:메세지|메시지|이메일|메일)(?:가|이)?\s*(?:전달|전송|발송)되었습니다",
        text,
    )
    return int(match[1]) if match else None


def parse_email_history(html):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        links = [
            a
            for a in row.select("a[href]")
            if urlsplit(a["href"]).path == "/local/ubmessage/email_send.php"
        ]
        for link in links:
            query = parse_qs(urlsplit(link["href"]).query)
            if query.get("id") == [COURSE] and query.get("eid", [""])[0].isdigit():
                student_ids = set()
                for node in cells[2].select("[title]"):
                    match = re.search(r"\((\d{7,12})\)", node["title"])
                    if match:
                        student_ids.add(match[1])
                records.append(
                    {
                        "eid": query["eid"][0],
                        "subject": normalize(cells[1].get_text()),
                        "student_ids": student_ids,
                    }
                )
    return records


class PlatoNotificationTransport:
    @classmethod
    def from_session(cls, session, base_dir):
        value = cls.__new__(cls)
        value.session = session
        value.base_dir = Path(base_dir)
        value.ledger = DeliveryLedger(
            value.base_dir / ".runtime/notification-deliveries.sqlite3"
        )
        value.student_ids = {}
        return value

    def __init__(self, driver, base_dir):
        self.session = requests.Session()
        for cookie in driver.get_cookies():
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=HOST,
                path=cookie.get("path", "/"),
            )
        self.session.headers["User-Agent"] = driver.execute_script(
            "return navigator.userAgent"
        )
        self.base_dir = Path(base_dir)
        self.ledger = DeliveryLedger(
            self.base_dir / ".runtime/notification-deliveries.sqlite3"
        )
        self.student_ids = {}

    def close(self):
        self.session.close()
        self.ledger.close()

    def _get(self, url, **kwargs):
        for attempt in range(2):
            try:
                return self.session.get(url, **kwargs)
            except requests.RequestException:
                if attempt:
                    raise
                time.sleep(0.3)

    def prepare(self, kind, recipients):
        if kind not in ("message", "email"):
            raise ValueError("Unknown notification kind")
        page = (
            BASE
            + ("send.php" if kind == "message" else "email_send.php")
            + "?id="
            + COURSE
            + "&lang=ko"
        )
        snapshot = getattr(self, "form_snapshot", {}).get(kind)
        if snapshot is not None:
            hidden, targets = snapshot
        else:
            with request_deadline(45), self._get(page, timeout=(5, 30)) as response:
                if (
                    response.status_code != 200
                    or urlsplit(response.url).netloc != HOST
                    or "/login/" in response.url
                ):
                    raise NotificationError("Notification login failed")
                hidden, targets = parse_send_form(response.text, kind, page)
        self.last_form = (hidden, targets)
        ids, missing = resolve_recipients(targets, recipients, kind)
        if kind == "message":
            for target in targets:
                match = re.search(r"\((\d{7,12})\)", target["title"] or target["text"])
                if match:
                    self.student_ids[target["id"]] = match[1]
        return hidden, ids, missing

    def email_history(self, *, fresh=False):
        if getattr(self, "history_reader", None):
            return self.history_reader(fresh=fresh)
        with (
            request_deadline(45),
            self._get(
                BASE + "email_log.php?id=" + COURSE,
                timeout=(5, 30),
                allow_redirects=False,
            ) as response,
        ):
            if response.status_code != 200:
                raise NotificationError("Email receipt history unavailable")
            return parse_email_history(response.text)

    def attempt_path(self, key):
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Invalid delivery key")
        root = self.base_dir / ".runtime/notification-attempts"
        root.mkdir(mode=0o700, exist_ok=True)
        return root / (key + ".json")

    def recover_email(self, key):
        path = self.attempt_path(key)
        if not path.exists():
            return False
        value = json.loads(path.read_text(encoding="utf-8"))
        try:
            records = self.email_history(fresh=True)
        except (NotificationError, requests.RequestException, TimeoutError):
            return False
        found = next(
            (
                record
                for record in records
                if record["eid"] not in value["previous_email_ids"]
                and record["subject"] == value["subject"]
                and record["student_ids"] == set(value["student_ids"])
            ),
            None,
        )
        if not found:
            return False
        self.ledger.finish(
            key, "sent", time.time() - value["started_at"], "email_log:" + found["eid"]
        )
        return True

    def recover_event(self, event_key):
        event = hashlib.sha256(
            json.dumps(["email", event_key], ensure_ascii=False).encode()
        ).hexdigest()
        rows = self.ledger.connection.execute(
            "SELECT state,delivery_id FROM delivery_recipients WHERE event=? AND kind='email'",
            (event,),
        ).fetchall()
        if not rows:
            return None
        for key in {row[1] for row in rows if row[0] != "sent"}:
            self.recover_email(key)
        states = self.ledger.connection.execute(
            "SELECT state FROM delivery_recipients WHERE event=? AND kind='email'",
            (event,),
        ).fetchall()
        return (
            len(states) if states and all(row[0] == "sent" for row in states) else False
        )

    def validated_recipients(self, kind, recipients):
        hidden, ids, missing = self.prepare(kind, recipients)
        path = self.base_dir / ".runtime" / f"notification-missing-{kind}.json"
        import tempfile

        descriptor, temporary = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".pending", dir=path.parent
        )
        os.close(descriptor)
        pending = Path(temporary)
        pending.write_text(
            json.dumps(
                {
                    "updated": time.time(),
                    "kind": kind,
                    "requested": len(recipients),
                    "missing_hashes": missing,
                }
            ),
            encoding="utf-8",
        )
        os.chmod(pending, 0o600)
        os.replace(pending, path)
        if len(missing) >= 10 and len(missing) / max(1, len(recipients)) >= 0.25:
            raise NotificationError("Recipient directory mismatch")
        return hidden, ids, missing

    def send(
        self,
        kind,
        recipients,
        subject,
        body,
        event_key,
        *,
        test_only_id=None,
        expected_ids=None,
    ):
        started = time.monotonic()
        hidden, ids, missing = self.validated_recipients(kind, recipients)
        if test_only_id and ids != [str(test_only_id)]:
            raise NotificationError("Self-test recipient mismatch")
        if expected_ids is not None and set(ids) != set(expected_ids):
            raise NotificationError("Queued recipient list changed")
        sent = 0
        event, pending_ids, deduplicated, unknown = self.ledger.pending_recipients(
            kind, event_key, ids
        )
        if unknown and kind == "email":
            for key in unknown:
                self.recover_email(key)
            event, pending_ids, deduplicated, unknown = self.ledger.pending_recipients(
                kind, event_key, ids
            )
        if unknown:
            raise NotificationOutcomeUnknown("Prior delivery needs verification")
        for batch in [pending_ids] if pending_ids else []:
            key = hashlib.sha256(
                json.dumps([kind, event_key, batch], ensure_ascii=False).encode()
            ).hexdigest()
            state = self.ledger.state(key)
            if state == "sent":
                deduplicated += len(batch)
                continue
            if state is not None:
                raise NotificationOutcomeUnknown("Prior delivery needs verification")
            previous_email_ids = set()
            expected_student_ids = set()
            if kind == "email":
                if any(value not in self.student_ids for value in batch):
                    self.prepare("message", [])
                if any(value not in self.student_ids for value in batch):
                    raise NotificationError("Email recipient identity unavailable")
                expected_student_ids = {self.student_ids[value] for value in batch}
                previous_email_ids = {record["eid"] for record in self.email_history()}
            state = self.ledger.claim(key, kind, len(batch), event, batch)
            if state == "sent":
                deduplicated += len(batch)
                continue
            if state != "new":
                raise NotificationOutcomeUnknown(
                    "Concurrent delivery needs verification"
                )
            if kind == "email":
                path = self.attempt_path(key)
                path.write_text(
                    json.dumps(
                        {
                            "subject": normalize(subject),
                            "student_ids": sorted(expected_student_ids),
                            "previous_email_ids": sorted(previous_email_ids),
                            "started_at": time.time(),
                        }
                    ),
                    encoding="utf-8",
                )
                os.chmod(path, 0o600)
            payload = list(hidden.items()) + [("tousers[]", value) for value in batch]
            if kind == "message":
                payload.append(("message", body))
            else:
                payload.extend([("subject", subject), ("message[text]", body)])
            attempt = time.monotonic()
            try:
                with request_deadline(620 if kind == "email" else 90):
                    response = self.session.post(
                        BASE + "action.php",
                        data=payload,
                        timeout=(5, 600 if kind == "email" else 80),
                        allow_redirects=False,
                        headers={
                            "Referer": BASE
                            + ("send.php" if kind == "message" else "email_send.php")
                            + "?id="
                            + COURSE
                            + "&lang=ko"
                        },
                    )
            except (requests.RequestException, TimeoutError) as exc:
                self.ledger.finish(
                    key, "unknown", time.monotonic() - attempt, type(exc).__name__
                )
                if kind == "email" and self.recover_email(key):
                    sent += len(batch)
                    continue
                raise NotificationOutcomeUnknown(
                    "Delivery response unavailable"
                ) from None
            with response:
                count = response_recipient_count(response.text)
                confirmed = response_is_success(response) and (
                    count is None or count == len(batch)
                )
                detail = "unrecognized_response"
                if kind == "email" and response.status_code in (200, 302, 303):
                    try:
                        records = self.email_history(fresh=True)
                        confirmed = any(
                            record["eid"] not in previous_email_ids
                            and record["subject"] == normalize(subject)
                            and record["student_ids"] == expected_student_ids
                            for record in records
                        )
                    except (NotificationError, requests.RequestException, TimeoutError):
                        confirmed = False
                        detail = "email_history_unavailable"
                if confirmed:
                    detail = (
                        "email_log:"
                        + next(
                            record["eid"]
                            for record in records
                            if record["eid"] not in previous_email_ids
                            and record["subject"] == normalize(subject)
                            and record["student_ids"] == expected_student_ids
                        )
                        if kind == "email"
                        else "ack_count:" + str(count)
                    )
                    self.ledger.finish(key, "sent", time.monotonic() - attempt, detail)
                    sent += len(batch)
                else:
                    private = (
                        self.base_dir
                        / ".runtime"
                        / ("notification-response-" + key[:16] + ".html")
                    )
                    private.write_text(response.text[:200000], encoding="utf-8")
                    os.chmod(private, 0o600)
                    private.with_suffix(".json").write_text(
                        json.dumps(
                            {
                                "status": response.status_code,
                                "count": count,
                                "previous_email_ids": sorted(previous_email_ids),
                                "expected_student_ids": sorted(expected_student_ids),
                            }
                        ),
                        encoding="utf-8",
                    )
                    os.chmod(private.with_suffix(".json"), 0o600)
                    self.ledger.finish(
                        key, "unknown", time.monotonic() - attempt, detail
                    )
                    raise NotificationOutcomeUnknown(
                        "Delivery confirmation unavailable"
                    )
        return {
            "requested": len(recipients),
            "matched": len(ids),
            "missing": len(missing),
            "sent": sent,
            "deduplicated": deduplicated,
            "seconds": time.monotonic() - started,
            "transport": "authenticated_form_post",
        }
