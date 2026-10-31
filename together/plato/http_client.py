"""읽기 작업과 알림 전송에 재사용할 PLATO HTTP 세션."""

from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import pandas as pd
import requests

from together.plato.auth import http_login_session
from together.plato.notifications import NotificationAuthenticationError
from together.config import BASE_DIR
from together.plato.login_page import is_login_page


ROOT = "https://dev-plato.pusan.ac.kr/"
COURSE = ROOT + "course/view.php?id=157301"
PARTICIPANTS = ROOT + "user/users.php?id=157301"
SURVEY = ROOT + "mod/feedback/view.php?id=2007210&lang=ko"


class PlatoHttpClient:
    def __init__(self, session, cookie_path=None):
        self.session = session
        self.cookie_path = Path(cookie_path) if cookie_path else None
        self._course_html = None

    @staticmethod
    def _allowed_cookie_domain(value):
        return str(value or "").lstrip(".") in (
            "dev-plato.pusan.ac.kr",
            "pusan.ac.kr",
        )

    @classmethod
    def _save_cookies(cls, session, path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = path.with_suffix(".pending")
        values = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie in session.cookies
            if cls._allowed_cookie_domain(cookie.domain)
        ]
        pending.write_text(json.dumps(values), encoding="utf-8")
        pending.chmod(0o600)
        pending.replace(path)

    @classmethod
    def _cached_session(cls, path):
        if not path.exists():
            return None
        session = requests.Session()
        session.headers["User-Agent"] = os.getenv("USER_AGENT") or "Mozilla/5.0"
        try:
            for item in json.loads(path.read_text(encoding="utf-8")):
                if not cls._allowed_cookie_domain(item.get("domain")):
                    raise ValueError("PLATO cookie scope changed")
                session.cookies.set(
                    item["name"],
                    item["value"],
                    domain=item.get("domain") or "dev-plato.pusan.ac.kr",
                    path=item.get("path") or "/",
                    secure=bool(item.get("secure")),
                    expires=item.get("expires"),
                )
            return session
        except Exception:
            session.close()
            path.unlink(missing_ok=True)
            return None

    @classmethod
    def login(cls, base_dir=BASE_DIR):
        path = Path(base_dir) / ".runtime/plato-http-cookies.json"
        session = cls._cached_session(path)
        if session is None:
            session = http_login_session()
            cls._save_cookies(session, path)
        return cls(session, path)

    def close(self):
        self.session.close()

    def get(self, url, **kwargs):
        for attempt in range(2):
            try:
                response = self.session.get(url, **kwargs)
                response.raise_for_status()
                if is_login_page(response.url, response.text):
                    response.close()
                    if attempt == 0 and self.cookie_path is not None:
                        self.session.close()
                        self.session = http_login_session()
                        self._save_cookies(self.session, self.cookie_path)
                        self._course_html = None
                        continue
                    raise NotificationAuthenticationError("PLATO login required")
                return response
            except requests.RequestException:
                if attempt:
                    raise
                time.sleep(0.3)

    def post_once(self, url, **kwargs):
        try:
            response = self.session.post(url, **kwargs)
        except requests.RequestException as exc:
            raise HttpSubmissionUnknown("PLATO post result is unknown") from exc
        try:
            response.raise_for_status()
            if is_login_page(response.url, response.text):
                raise HttpSubmissionUnknown("PLATO session expired during post")
            return response
        except BaseException:
            response.close()
            raise

    def participant_count(self):
        with self.get(PARTICIPANTS, timeout=(5, 30)) as response:
            soup = BeautifulSoup(response.text, "html.parser")
        cell = soup.find("td", class_="cell c1 number text-center column-number")
        if cell is None:
            raise ValueError("Participant count unavailable")
        return int(cell.get_text(strip=True).replace(",", ""))

    def course_html(self, *, fresh=False):
        if fresh or self._course_html is None:
            with self.get(COURSE, timeout=(5, 30)) as response:
                self._course_html = response.text
        return self._course_html

    def displayed_participant_count(self):
        text = BeautifulSoup(self.course_html(), "html.parser").get_text(" ", strip=True)
        match = re.search(r"현재\s*([\d,]+)\s*명이 함께하고 있습니다", text)
        return int(match.group(1).replace(",", "")) if match else None

    def board_url(self, board_name):
        for attempt in range(2):
            soup = BeautifulSoup(self.course_html(fresh=bool(attempt)), "html.parser")
            for link in soup.select("a[href]"):
                instance = link.select_one("span.instancename")
                if instance and board_name in instance.get_text(" ", strip=True):
                    return urljoin(COURSE, link["href"])
        raise ValueError(f"Notice board unavailable: {board_name}")

    @staticmethod
    def _form_values(form):
        values = {}
        for node in form.select("input[name]"):
            kind = node.get("type", "text").lower()
            if kind in ("submit", "button", "file", "reset"):
                continue
            if kind in ("checkbox", "radio") and not node.has_attr("checked"):
                continue
            values[node["name"]] = node.get("value", "")
        for node in form.select("select[name]"):
            selected = node.select_one("option[selected]") or node.select_one("option")
            values[node["name"]] = selected.get("value", "") if selected else ""
        for node in form.select("textarea[name]"):
            values[node["name"]] = node.get_text()
        return values

    def create_notice(self, board_name, subject, content_html, *, before_submit=None):
        board_url = self.board_url(board_name)
        with self.get(board_url, timeout=(5, 30)) as board:
            soup = BeautifulSoup(board.text, "html.parser")
            write = next(
                (
                    link
                    for link in soup.select("a[href]")
                    if " ".join(link.get_text(" ", strip=True).split()) == "쓰기"
                ),
                None,
            )
            if write is None:
                raise HttpPostUnsupported("Write link unavailable")
            editor_url = urljoin(board.url, write["href"])
        with self.get(editor_url, timeout=(5, 30)) as editor:
            editor_soup = BeautifulSoup(editor.text, "html.parser")
            form = next(
                (
                    item
                    for item in editor_soup.select("form")
                    if item.select_one('[name="subject"]')
                    and item.select_one('[name="content[text]"]')
                ),
                None,
            )
            if form is None:
                raise HttpPostUnsupported("Write form unavailable")
            data = self._form_values(form)
            data["subject"] = subject
            data["content[text]"] = content_html
            data["content[format]"] = data.get("content[format]", "1")
            submit = form.select_one('[name="submitbutton"]')
            if submit is not None:
                data["submitbutton"] = submit.get("value", "")
            action = urljoin(editor.url, form.get("action", ""))
        if before_submit is not None:
            before_submit()
        response = self.post_once(
            action,
            data=data,
            headers={"Referer": editor_url, "Origin": ROOT.rstrip("/")},
            timeout=(5, 60),
        )
        target = urlsplit(response.url)
        if target.netloc != "dev-plato.pusan.ac.kr" or target.path != "/mod/ubboard/article.php":
            response.close()
            raise HttpSubmissionUnknown("Published article address unavailable")
        return response

    def download_survey(self, directory):
        with self.get(SURVEY, timeout=(5, 30)) as response:
            soup = BeautifulSoup(response.text, "html.parser")
        count = soup.select_one(".feedback_info_value")
        if count is None:
            raise ValueError("Survey response count unavailable")
        response_link = next(
            (
                link
                for link in soup.select("a[href]")
                if " ".join(link.get_text(" ", strip=True).split()) == "응답 보기"
            ),
            None,
        )
        if response_link is None:
            raise ValueError("Survey response page unavailable")
        detail_url = urljoin(SURVEY, response_link["href"])
        with self.get(detail_url, timeout=(5, 30)) as detail:
            detail_soup = BeautifulSoup(detail.text, "html.parser")
            form = next(
                (
                    item
                    for item in detail_soup.select("form")
                    if item.select_one('[name="download"]')
                ),
                None,
            )
            if form is None:
                raise ValueError("Survey download form unavailable")
            params = self._form_values(form)
            button = form.select_one('[name="download"]')
            if button.name in ("button", "input") and button.get("value"):
                params["download"] = button["value"]
            action = urljoin(detail.url, form.get("action", ""))
        with self.get(action, params=params, timeout=(5, 60)) as download:
            if "text/html" in download.headers.get("Content-Type", "").lower():
                raise ValueError("Survey download returned HTML")
            frame = pd.read_csv(BytesIO(download.content), encoding="utf-8-sig", dtype={"학번": str})

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        full = directory / "알림 신청하기.csv"
        frame.to_csv(full, index=False, encoding="utf-8-sig")
        os.chmod(full, 0o600)

        message = frame[
            ["이름", "학번", "(선택/복수 가능) 학과", "(선택/복수 가능) 알림 설정"]
        ].copy()
        message["Plato 쪽지 여부"] = message["(선택/복수 가능) 알림 설정"].apply(
            lambda value: "Plato 쪽지" in str(value)
        )
        message_path = directory / "message.csv"
        message.to_csv(message_path, index=False, encoding="utf-8-sig")
        os.chmod(message_path, 0o600)

        email = frame[
            ["이름", "(선택/복수 가능) 학과", "(선택/복수 가능) 알림 설정", "이메일 주소"]
        ].copy()
        email["Email 여부"] = email["(선택/복수 가능) 알림 설정"].apply(
            lambda value: "이메일" in str(value)
        )
        email_path = directory / "email.csv"
        email.to_csv(email_path, index=False, encoding="utf-8-sig")
        os.chmod(email_path, 0o600)
        return count.get_text(strip=True)


class HttpPostUnsupported(RuntimeError):
    """저장 요청을 보내기 전에 HTTP 작성 경로를 사용할 수 없음."""


class HttpSubmissionUnknown(RuntimeError):
    """저장 요청 이후 결과를 확정할 수 없어 재시도하면 안 됨."""

