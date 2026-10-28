"""제목 검색의 모든 페이지를 읽고 지난 공지의 별표만 제거한다."""

from datetime import datetime
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from together.notices.intelligence.post_editor import (
    PlatoPostEditor, PostEditError, file_names, html_signature,
    snapshot_signature, validate_article_url,
)


def collect_old_stars(session, board_url, today):
    parts = urlsplit(board_url)
    board_id = parse_qs(parts.query)["id"]
    query = parse_qs(parts.query)
    query.update(keyfield=["subject"], keyword=["⭐"])
    query.pop("page", None)
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))
    pending, visited, signatures, posts = [1], set(), set(), {}
    while pending:
        page = min(pending)
        pending.remove(page)
        if page in visited:
            continue
        visited.add(page)
        with session.get(base, params={"page": page}, timeout=(10, 60)) as response:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.select_one('input[name="username"]'):
                raise PostEditError("Star search requires login")
        entries = []
        for row in soup.select("tbody tr"):
            link = next((a for a in row.select('a[href]') if urlsplit(urljoin(base, a["href"])).path == "/mod/ubboard/article.php"), None)
            if link is None:
                continue
            url = urljoin(base, link["href"])
            ident = validate_article_url(url)
            if [ident["board_id"]] != board_id:
                raise PostEditError("Star search board mismatch")
            title = link.get_text(" ", strip=True)
            entries.append(ident["post_id"])
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", row.get_text(" ", strip=True))
            if title.startswith("⭐") and not dates:
                raise PostEditError("Starred post date needs inspection")
            if title.startswith("⭐") and dates and dates[0] < today:
                posts[ident["post_id"]] = url
        signature = tuple(entries)
        if signature and signature in signatures:
            raise PostEditError("Star search returned a repeated page")
        signatures.add(signature)
        for link in soup.select('a[href]'):
            target = urlsplit(urljoin(base, link["href"]))
            params = parse_qs(target.query)
            number = params.get("page", [""])[0]
            if target.netloc == parts.netloc and target.path == parts.path and params.get("id") == board_id and number.isdigit() and int(number) >= 1 and int(number) not in visited:
                pending.extend(value for value in range(1, int(number) + 1) if value not in visited)
    return list(posts.values()), sorted(visited)


def remove_star(service, article_url, base_dir):
    editor = PlatoPostEditor(service, Path(base_dir) / ".runtime/intelligence/edit-backups")
    editor._open_edit(article_url)

    def read_fields():
        return {
            "subject": service.driver.find_element(By.NAME, "subject").get_attribute("value"),
            "body_html": service.driver.find_element(By.ID, "id_contenteditable").get_attribute("innerHTML"),
            "files": sorted(file_names(service.driver)),
            "notice": editor._notice_state(),
        }

    before = read_fields()
    if not before["subject"].startswith("⭐"):
        return None
    subject = before["subject"][1:].strip()
    root = Path(base_dir) / ".runtime/star-cleanup"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / (validate_article_url(article_url)["post_id"] + ".json")
    target.write_text(json.dumps({"article_url": article_url, "before": before}, ensure_ascii=False), encoding="utf-8")
    target.chmod(0o600)
    field = service.driver.find_element(By.NAME, "subject")
    service.driver.execute_script("arguments[0].value=arguments[1];for(const t of ['input','change'])arguments[0].dispatchEvent(new Event(t,{bubbles:true}));", field, subject)
    service.driver.find_element(By.NAME, "submitbutton").click()
    try:
        WebDriverWait(service.driver, 45).until(EC.staleness_of(field))
    except Exception:
        pass
    editor._open_edit(article_url)
    after = read_fields()
    if (after["subject"] != subject or html_signature(before["body_html"]) != html_signature(after["body_html"]) or before["files"] != after["files"] or before["notice"] != after["notice"]):
        raise PostEditError("Star-only edit needs verification")
    from together.notices.intelligence.enrichment import PublishedNoticeStore
    store = PublishedNoticeStore(base_dir)
    ident = validate_article_url(article_url)
    for record in store.load():
        if validate_article_url(record["article_url"]) != ident:
            continue
        hashes = record.get("file_hashes", {})
        if sorted(hashes) == before["files"]:
            snapshot = dict(before, files=[{"name": name, "sha256": value} for name, value in hashes.items()])
            if snapshot_signature(snapshot) == record["signature"]:
                snapshot["subject"] = subject
                store.put(dict(record, signature=snapshot_signature(snapshot)))
    target.write_text(json.dumps({"article_url": article_url, "before": before, "after": after, "verified": True}, ensure_ascii=False), encoding="utf-8")
    target.chmod(0o600)
    return subject
