"""학생지원시스템의 정상 통합로그인과 읽기 전용 게시판 조회."""

import atexit
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urljoin

from together.config import BASE_DIR

MENUS = {"000000000000386": "학생지원시스템 공지사항", "000000000000062": "학생지원시스템 장학 공지사항"}
_client = None


def menu_for(url):
    parsed = urlsplit(url)
    code = parse_qs(parsed.query).get("menuCD", [""])[0]
    return code if parsed.hostname == "onestop.pusan.ac.kr" and parsed.path == "/page" and code in MENUS else None


class OnestopError(RuntimeError):
    pass


def safe_rows(reply):
    """작성자·학번·이메일·내부 비밀번호 필드는 수집 자료에 포함하지 않는다."""
    rows = reply.get("data")
    if not isinstance(rows, list) or not isinstance(reply.get("pageInfo"), dict):
        raise OnestopError("Student support list response is incomplete")
    allowed = ("POSTING_SEQ_NO", "TITLE_CONTENT", "CONTENT", "NOTICE_FG", "RN", "INS_DT", "POSTING_START_DT", "POSTING_CLOSE_DT")
    result = []
    for row in rows:
        if row.get("SECRET_FG") == "Y":
            raise OnestopError("Private student support article requires review")
        if not str(row.get("POSTING_SEQ_NO", "")).isdigit() or not row.get("TITLE_CONTENT"):
            raise OnestopError("Student support article identity is missing")
        item = {key: row.get(key) for key in allowed}
        item["files"] = [{key: file.get(key) for key in ("ORIGIN_FILE_NM", "FILE_SIZE", "PARAM_CODE", "ATTACH_FILE_SEQ_NO")} for file in row.get("bbsFileList") or []]
        result.append(item)
    return result


class OnestopClient:
    def __init__(self):
        self.browser = None
        self.menu = None
        self.articles = {}
        self.cookie_path = Path(BASE_DIR) / ".runtime/onestop-cookies.json"

    def _restore_cookies(self, driver):
        if not self.cookie_path.exists():
            return False
        try:
            values = json.loads(self.cookie_path.read_text(encoding="utf-8"))
            cookies = []
            for item in values:
                domain = str(item.get("domain") or "").lstrip(".")
                if domain not in ("onestop.pusan.ac.kr", "pusan.ac.kr"):
                    raise OnestopError("Stored student support cookie scope changed")
                value = {
                    key: item[key]
                    for key in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")
                    if key in item
                }
                if item.get("expiry"):
                    value["expires"] = int(item["expiry"])
                cookies.append(value)
            if not cookies:
                return False
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setCookies", {"cookies": cookies})
            return True
        except Exception:
            self.cookie_path.unlink(missing_ok=True)
            return False

    def _save_cookies(self, driver):
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = self.cookie_path.with_suffix(".pending")
        values = [
            item
            for item in driver.get_cookies()
            if str(item.get("domain") or "").lstrip(".")
            in ("onestop.pusan.ac.kr", "pusan.ac.kr")
        ]
        pending.write_text(json.dumps(values), encoding="utf-8")
        pending.chmod(0o600)
        pending.replace(self.cookie_path)

    def close(self):
        if self.browser is not None:
            self.browser.close()
            self.browser = None
            self.menu = None

    def _open(self, code):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from together.plato.service import WriteNoticeService

        if code not in MENUS:
            raise OnestopError("Unsupported student support menu")
        if self.browser is None:
            browser = WriteNoticeService.__new__(WriteNoticeService)
            browser.driver = None
            browser.window_size = os.getenv("WINDOW_SIZE", "1280,900")
            browser.user_agent = os.getenv("USER_AGENT", "")
            browser.chrome_driver_path = os.getenv("CHROME_DRIVER_PATH")
            browser._title_maintenance = True
            self.browser = browser
            browser.init_driver()
            self._restore_cookies(browser.driver)
        d = self.browser.driver
        url = "https://onestop.pusan.ac.kr/page?menuCD=" + code
        d.get(url)
        WebDriverWait(d,25).until(lambda d: d.execute_script('return typeof scwin !== "undefined" && !!scwin.menuInfo'))
        if not d.execute_script("return !!scwin.loginUser.userId"):
            if not os.getenv("PLATO_ID") or not os.getenv("PLATO_PW"):
                raise OnestopError("Student support credentials are not configured")
            d.execute_script("ssoOpen()")
            WebDriverWait(d,25).until(lambda d: urlsplit(d.current_url).hostname == "login.pusan.ac.kr")
            d.find_element(By.LINK_TEXT, "아이디 로그인").click()
            WebDriverWait(d,10).until(lambda d: d.find_element(By.ID, "login_id").is_displayed())
            d.find_element(By.ID, "login_id").send_keys(os.environ["PLATO_ID"])
            d.find_element(By.ID, "login_pw").send_keys(os.environ["PLATO_PW"])
            d.find_element(By.ID, "btnLogin").click()
            WebDriverWait(d,35).until(lambda d: urlsplit(d.current_url).hostname == "onestop.pusan.ac.kr" or d.find_elements(By.LINK_TEXT, "다음에 변경하기"))
            if urlsplit(d.current_url).hostname == "login.pusan.ac.kr":
                d.find_element(By.LINK_TEXT, "다음에 변경하기").click()
                WebDriverWait(d,35).until(lambda d: urlsplit(d.current_url).hostname == "onestop.pusan.ac.kr")
            d.get(url)
            WebDriverWait(d,25).until(lambda d: d.execute_script('return typeof scwin !== "undefined" && !!scwin.loginUser.userId'))
        self._save_cookies(d)
        if d.execute_script("return scwin.menuCd") != code:
            raise OnestopError("Student support menu changed")
        self.menu = code

    def page(self, code, index=0):
        if self.menu != code or self.browser is None:
            self._open(code)
        d = self.browser.driver
        d.set_script_timeout(35)
        reply = d.execute_async_script("""
            const done=arguments[arguments.length-1];
            gfn_ajax_request({url:'/bbs/selectList', reqData:{pageIndex:arguments[0],pageSize:10},
                success:function(data,response){done(response);}});
        """, index)
        rows = safe_rows(reply)
        for row in rows:
            self.articles[(code, int(row["POSTING_SEQ_NO"]))] = row
        return rows, reply["pageInfo"]

    def recent(self, code, boundary):
        """고정 글이 첫 페이지를 채워도 일반 글의 경계까지 계속 읽는다."""
        found = {}
        previous = None
        index = 0
        count = 0
        while True:
            rows, info = self.page(code, index)
            fingerprint = tuple(int(row["POSTING_SEQ_NO"]) for row in rows)
            if index and fingerprint and fingerprint == previous:
                raise OnestopError("Student support pagination did not advance")
            previous = fingerprint
            count = int(info.get("totCnt", 0))
            for row in rows:
                found[int(row["POSTING_SEQ_NO"])] = row
            ordinary = [row for row in rows if row.get("NOTICE_FG") != "Y"]
            if ordinary and all(int(row["POSTING_SEQ_NO"]) <= boundary for row in ordinary):
                break
            if not rows or index + 1 >= int(info.get("totPage", 1)):
                break
            index += 1
        selected = [found[key] for key in sorted(found) if key > boundary]
        return selected, max([boundary, *found]), count

    def attachment_session(self):
        import requests
        session = requests.Session()
        for cookie in self.browser.driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie.get("path", "/"), secure=cookie.get("secure", True))
        return session

    def article(self, url, category=None, directory="downloads"):
        from bs4 import BeautifulSoup
        from together.crawler.announcements import Announcement, clean_title
        from together.crawler.files import download_attachment
        from together.notices.intelligence.metadata import extract_metadata
        from together.operations.events import event
        from together.config import HTTP_TIMEOUT

        code = menu_for(url)
        if code is None:
            raise OnestopError("Unsupported student support article")
        if self.browser is None or self.menu != code:
            self._open(code)
        with self.attachment_session() as session:
            response = session.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            title = soup.select_one(".board-view-title h4")
            content = soup.select_one(".board-view-cont")
            if title is None or content is None:
                raise OnestopError("Student support article content is missing")
            for node in content.select("script,iframe,form"):
                node.decompose()
            for node in content.select("img[src],a[href]"):
                attr = "src" if node.name == "img" else "href"
                node[attr] = urljoin(response.url, node[attr])
            metadata = extract_metadata(soup, url)
            metadata["inline_image_count"] = len(content.select("img"))
            metadata["attachment_scan_complete"] = soup.select_one(".board-view-files") is not None
            files = []
            if category is not None and category != "해당없음":
                for link in soup.select('.board-view-files a[href*="/file/download.do"]'):
                    result = download_attachment(urljoin(response.url, link["href"]), link.get_text(" ",strip=True), referer=url, directory=directory, http_client=session)
                    if result and result["path"] not in files:
                        files.append(result["path"])
                        event("첨부", f"파일 다운로드 완료: {result['filename']}", plain=True, source_url=url, filename=result["filename"], bytes=result["bytes"], sha256=result["sha256"])
            return Announcement(clean_title(title.get_text(" ",strip=True)), str(content), content.get_text(" ",strip=True), "", url, files, metadata)


def collect(page, ordinal, event, record_board_result):
    code = menu_for(page.page_url)
    try:
        rows, maximum, count = client().recent(code, page.number)
    except Exception as exc:
        client().close()
        record_board_result(False)
        event("수집", f"학생지원시스템 조회 실패: {type(exc).__name__} - {page.page_url}", level="WARN", source_url=page.page_url)
        return [], page.number
    record_board_result(True)
    urls = [f"https://onestop.pusan.ac.kr/page?menuCD={code}&mode=DETAIL&seq={row['POSTING_SEQ_NO']}" for row in rows]
    event("수집", f"{ordinal}. {getattr(page, 'display_name', page.department)} {count}", plain=True)
    event("수집", f"💡{len(urls)}개의 New Announcment!: {page.page_url}" if urls else f"새로 추가된 공지사항 없음: {page.page_url}", plain=True)
    return urls, maximum


def client():
    global _client
    if _client is None:
        _client = OnestopClient()
        atexit.register(_client.close)
    return _client
