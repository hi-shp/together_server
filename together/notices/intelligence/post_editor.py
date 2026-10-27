"""기존 첨부파일을 보존하며 게시물을 수정하고 저장 결과를 다시 확인한다."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from bs4 import BeautifulSoup
import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

HOST = "dev-plato.pusan.ac.kr"
MAX_BACKUP_BYTES = 128 * 1024 * 1024
MAX_OPERATION_BYTES = 32 * 1024 * 1024


class PostEditError(RuntimeError):
    pass


def file_names(driver):
    # AJAX가 목록을 교체하는 동안 오래된 요소 참조를 사용하지 않는다.
    return driver.execute_script(
        "return Array.from(document.querySelectorAll('.filemanager .fp-file .fp-filename')).map(node=>node.textContent.trim())"
    )


def wait_file_manager(driver):
    WebDriverWait(driver, 45).until(
        lambda browser: browser.execute_script(
            """
        const manager = document.querySelector('.filemanager');
        return manager && !manager.classList.contains('fm-loading') && !manager.classList.contains('fm-updating');
    """
        )
    )


def upload_file_in_manager(driver, path):
    path = Path(path)
    wait_file_manager(driver)
    if path.name in file_names(driver):
        raise PostEditError("Attachment filename already exists")
    driver.find_element(By.CSS_SELECTOR, ".filemanager .fp-btn-add a").click()
    file_input = WebDriverWait(driver, 20).until(
        lambda browser: next(
            (
                node
                for node in browser.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                if node.is_displayed()
            ),
            False,
        )
    )
    file_input.send_keys(str(path.resolve()))
    button = WebDriverWait(driver, 20).until(
        lambda browser: next(
            (
                node
                for node in browser.find_elements(
                    By.CSS_SELECTOR, "button.fp-upload-btn"
                )
                if node.is_displayed()
            ),
            False,
        )
    )
    button.click()
    WebDriverWait(driver, 45).until(lambda browser: path.name in file_names(browser))
    wait_file_manager(driver)


def set_post_fields(driver, subject, body_html):
    if not subject.strip() or not body_html.strip():
        raise PostEditError("Empty post fields")
    driver.execute_script(
        """
        const subject=document.querySelector('input[name="subject"]');
        const editor=document.getElementById('id_contenteditable');
        const textarea=document.querySelector('textarea[name="content[text]"]');
        subject.value=arguments[0];editor.innerHTML=arguments[1];textarea.value=arguments[1];
        for(const node of [subject,editor,textarea]){
          node.dispatchEvent(new Event('input',{bubbles:true}));
          node.dispatchEvent(new Event('change',{bubbles:true}));
          node.dispatchEvent(new Event('blur',{bubbles:true}));
        }
    """,
        subject,
        body_html,
    )


def validate_article_url(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.netloc != HOST
        or parsed.path != "/mod/ubboard/article.php"
        or not all(
            len(query.get(key, [])) == 1 and query[key][0].isdigit()
            for key in ("id", "bwid")
        )
    ):
        raise PostEditError("Unsupported article URL")
    return {"board_id": query["id"][0], "post_id": query["bwid"][0]}


def html_signature(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for node in soup.find_all(True):
        node.attrs = dict(sorted(node.attrs.items()))
    return hashlib.sha256(str(soup).strip().encode()).hexdigest()


def snapshot_signature(snapshot):
    data = {
        "subject": snapshot["subject"],
        "body": html_signature(snapshot["body_html"]),
        "files": sorted((item["name"], item["sha256"]) for item in snapshot["files"]),
        "notice": snapshot["notice"],
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class PlatoPostEditor:
    def __init__(self, service, backup_root):
        self.service = service
        self.driver = service.driver
        self.backup_root = Path(backup_root)
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backup_root, 0o700)

    def _wait_ready(self):
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.NAME, "subject"))
        )
        wait_file_manager(self.driver)

    def _file_names(self):
        return file_names(self.driver)

    def _open_edit(self, article_url, view_loaded=False):
        validate_article_url(article_url)
        if not view_loaded:
            self.service.safe_get(article_url, login_required=True)
        modify = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.modify"))
        )
        link = modify.get_attribute("href").replace("&amp;", "&")
        parsed = urlsplit(link)
        if (
            parsed.scheme != "https"
            or parsed.netloc != HOST
            or parsed.path != "/mod/ubboard/write.php"
        ):
            raise PostEditError("Unsupported edit URL")
        self.service.safe_get(link, login_required=True)
        self._wait_ready()
        expected = validate_article_url(article_url)
        for name, value in (
            ("id", expected["board_id"]),
            ("bwid", expected["post_id"]),
        ):
            if self.driver.find_element(By.NAME, name).get_attribute("value") != value:
                raise PostEditError("Edit target mismatch")

    def _attachment_links(self, article_url):
        expected = validate_article_url(article_url)
        links = []
        for href in self.driver.execute_script(
            "return Array.from(document.querySelectorAll('a[href]')).map(node=>node.href)"
        ):
            parsed = urlsplit(href)
            marker = f'/mod_ubboard/attachment/{expected["post_id"]}/'
            if (
                parsed.scheme == "https"
                and parsed.netloc == HOST
                and marker in parsed.path
            ):
                item = {"url": href, "name": unquote(parsed.path.rsplit("/", 1)[-1])}
                if item not in links:
                    links.append(item)
        return links

    def _download(self, item, destination=None, max_bytes=MAX_OPERATION_BYTES):
        session = requests.Session()
        try:
            for cookie in self.driver.get_cookies():
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=HOST,
                    path=cookie.get("path", "/"),
                )
            session.headers["User-Agent"] = self.driver.execute_script(
                "return navigator.userAgent"
            )
            size = 0
            digest = hashlib.sha256()
            handle = destination.open("wb") if destination else None
            try:
                with session.get(
                    item["url"], timeout=(5, 30), stream=True, allow_redirects=False
                ) as response:
                    if response.status_code != 200:
                        raise PostEditError("Attachment download failed")
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise PostEditError("Attachment exceeds verification limit")
                        digest.update(chunk)
                        if handle:
                            handle.write(chunk)
            finally:
                if handle:
                    handle.close()
                    os.chmod(destination, 0o600)
            return dict(item, bytes=size, sha256=digest.hexdigest())
        finally:
            session.close()

    def read(self, article_url, backup=False):
        validate_article_url(article_url)
        self.service.safe_get(article_url, login_required=True)
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.modify"))
        )
        links = self._attachment_links(article_url)
        if len(links) > 10:
            raise PostEditError("Too many existing attachments")
        folder = None
        if backup:
            self._prune()
            folder = self.backup_root / (
                datetime.now().strftime("%Y%m%d_%H%M%S") + "-" + uuid.uuid4().hex[:8]
            )
            folder.mkdir(mode=0o700)
        files = []
        for number, item in enumerate(links):
            destination = folder / f"attachment-{number:02d}.bin" if folder else None
            saved = self._download(
                item,
                destination,
                MAX_OPERATION_BYTES - sum(item["bytes"] for item in files),
            )
            if destination:
                saved["backup_file"] = str(destination)
            files.append(saved)
            if sum(item["bytes"] for item in files) > MAX_OPERATION_BYTES:
                raise PostEditError("Post exceeds verification limit")
        self._open_edit(article_url, view_loaded=True)
        if sorted(self._file_names()) != sorted(item["name"] for item in files):
            raise PostEditError("Attachment inventory mismatch")
        snapshot = {
            "article_url": article_url,
            "subject": self.driver.find_element(By.NAME, "subject").get_attribute(
                "value"
            ),
            "body_html": self.driver.find_element(
                By.ID, "id_contenteditable"
            ).get_attribute("innerHTML"),
            "notice": self.driver.find_element(By.NAME, "notice").is_selected(),
            "files": files,
        }
        snapshot["signature"] = snapshot_signature(snapshot)
        if folder:
            snapshot["backup_dir"] = str(folder)
            self._write_json(folder / "before.json", snapshot)
        return snapshot

    def _write_json(self, path, data):
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(path, 0o600)

    def _prune(self):
        folders = sorted(path for path in self.backup_root.iterdir() if path.is_dir())

        def size():
            return sum(
                path.stat().st_size
                for path in self.backup_root.rglob("*")
                if path.is_file()
            )

        remaining = len(folders)
        for folder in folders:
            verified = folder / "verified.json"
            if not verified.exists():
                verified = folder / "restored.json"
            if verified.exists() and (
                remaining > 10
                or (datetime.now().timestamp() - verified.stat().st_mtime) > 7 * 86400
            ):
                shutil.rmtree(folder)
                remaining -= 1
        if size() + MAX_OPERATION_BYTES > MAX_BACKUP_BYTES:
            raise PostEditError("Backup capacity reached")

    def _set_fields(self, subject, body_html):
        set_post_fields(self.driver, subject, body_html)

    def _upload(self, path):
        upload_file_in_manager(self.driver, path)

    def _delete_added(self, names):
        for name in names:
            files = self.driver.find_elements(By.CSS_SELECTOR, ".filemanager .fp-file")
            target = next(
                (
                    node
                    for node in files
                    if node.find_element(By.CSS_SELECTOR, ".fp-filename")
                    .get_attribute("textContent")
                    .strip()
                    == name
                ),
                None,
            )
            if target is None:
                raise PostEditError("Rollback attachment missing")
            target.click()
            button = WebDriverWait(self.driver, 20).until(
                lambda driver: next(
                    (
                        node
                        for node in driver.find_elements(
                            By.CSS_SELECTOR,
                            ".fp-file-delete button, button.fp-file-delete, .fp-file-delete",
                        )
                        if node.is_displayed()
                    ),
                    False,
                )
            )
            button.click()
            confirm = WebDriverWait(self.driver, 20).until(
                lambda driver: next(
                    (
                        node
                        for node in driver.find_elements(
                            By.CSS_SELECTOR, ".moodle-dialogue .fp-dlg-butconfirm"
                        )
                        if node.is_displayed()
                    ),
                    False,
                )
            )
            confirm.click()
            WebDriverWait(self.driver, 30).until(
                lambda driver: name not in self._file_names()
            )
            self._wait_ready()

    def _save(self, article_url):
        field = self.driver.find_element(By.NAME, "subject")
        self.driver.find_element(By.NAME, "submitbutton").click()
        try:
            WebDriverWait(self.driver, 45).until(EC.staleness_of(field))
        except Exception:
            # 저장 응답이 늦어도 재전송하지 않고 대상 글을 다시 읽는다.
            pass
        return self.read(article_url)

    def update(
        self, article_url, subject, body_html, files=(), expected_signature=None
    ):
        files = [Path(path) for path in files]
        if any(not path.is_file() for path in files):
            raise PostEditError("Upload file missing")
        if sum(path.stat().st_size for path in files) > MAX_OPERATION_BYTES:
            raise PostEditError("Upload exceeds limit")
        before = self.read(article_url, backup=True)
        if expected_signature and before["signature"] != expected_signature:
            raise PostEditError("Post changed after observation")
        old_names = {item["name"] for item in before["files"]}
        names = [path.name for path in files]
        if (
            len(set(names)) != len(names)
            or old_names & set(names)
            or len(old_names) + len(names) > 10
        ):
            raise PostEditError("Attachment name or count conflict")
        expected_files = {item["name"]: item["sha256"] for item in before["files"]}
        expected_files.update(
            {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        )
        for path in files:
            self._upload(path)
        self._set_fields(subject, body_html)
        after = self._save(article_url)
        result = {
            "article_url": article_url,
            "before": before,
            "after": after,
            "added_files": names,
        }
        if (
            after["subject"] != subject
            or html_signature(after["body_html"]) != html_signature(body_html)
            or {item["name"]: item["sha256"] for item in after["files"]}
            != expected_files
            or after["notice"] != before["notice"]
        ):
            self.restore(result)
            raise PostEditError("Saved post verification failed and was restored")
        self._write_json(
            Path(before["backup_dir"]) / "verified.json",
            {"after_signature": after["signature"], "added_files": names},
        )
        return result

    def restore(self, result):
        before, after = result["before"], result["after"]
        current = self.read(before["article_url"])
        if current["signature"] != after["signature"]:
            raise PostEditError("Post changed; restore blocked")
        original = {item["name"]: item for item in before["files"]}
        existing = {item["name"]: item for item in current["files"]}
        if set(existing) - set(original) != set(result["added_files"]):
            raise PostEditError("Unexpected attachments; restore blocked")
        damaged = [
            name
            for name in original
            if name in existing and original[name]["sha256"] != existing[name]["sha256"]
        ]
        self._delete_added(result["added_files"] + damaged)
        with tempfile.TemporaryDirectory(
            prefix="restore-", dir=self.backup_root
        ) as temporary:
            for name, item in original.items():
                if name in existing and name not in damaged:
                    continue
                source = Path(item["backup_file"])
                if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
                    raise PostEditError("Original backup checksum mismatch")
                destination = Path(temporary) / Path(name).name
                shutil.copyfile(source, destination)
                self._upload(destination)
        self._set_fields(before["subject"], before["body_html"])
        restored = self._save(before["article_url"])
        if restored["signature"] != before["signature"]:
            raise PostEditError("Restored post verification failed")
        self._write_json(
            Path(before["backup_dir"]) / "restored.json",
            {"restored": True, "signature": restored["signature"]},
        )
        return restored
