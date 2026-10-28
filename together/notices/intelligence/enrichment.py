"""직접 게시한 공지에 한해 더 충실한 원문을 찾아 보강한다."""

from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import tempfile
from bs4 import BeautifulSoup
from requests import RequestException

from .events import Notice, compare_notices
from .enrichment_quality import profile, text_gain, compatible, body_gain
from .post_editor import (
    PlatoPostEditor,
    PostEditError,
    PostEditRestoredError,
    validate_article_url,
    MAX_OPERATION_BYTES,
)
from .text import clean_display_title
from .quality import (
    display_title,
    inspect_rendered_post,
    sanitize_html,
)
from together.operations.events import event
from together.crawler.files import download_attachment, IMAGES


def _preserved_after_failed_enrichment(record, announcement, restored, base_dir):
    if restored["signature"] != record["signature"]:
        raise PostEditError("Restored enrichment signature mismatch")
    PublishedNoticeStore(base_dir).put(
        dict(
            record,
            ignored_sources=list(
                dict.fromkeys(record.get("ignored_sources", []) + [announcement.url])
            )[-10:],
            last_enrichment_failure={
                "source_url": announcement.url,
                "reason": "verified_rollback",
            },
        )
    )
    event(
        "보강",
        "게시글 보강 보류: 기존 제목·본문·첨부 복구 확인",
        level="WARN",
        outcome="verified_rollback",
        source_url=announcement.url,
    )
    return False


def as_notice(announcement, department):
    metadata = getattr(announcement, "metadata", {}) or {}
    return Notice(
        title=getattr(announcement, "original_title", announcement.title),
        observed_at=datetime.now().isoformat(timespec="seconds"),
        source_url=announcement.url,
        department=department,
        body_text=(BeautifulSoup(announcement.content_html, "html.parser").get_text(" ", strip=True) if getattr(announcement, "content_html", "") else announcement.content_text or "")[:6000],
        attachment_urls=[item["url"] for item in metadata.get("attachment_links", [])],
        metadata_complete=bool(metadata.get("attachment_scan_complete")),
        publication_time=metadata.get("publication_time") or "",
        content_profile=profile(sanitize_html(getattr(announcement, "content_html", ""), announcement.url)),
    )


def adds_information(old, new):
    if not compatible(old.body_text, new.body_text):
        return False
    # 파일 주소는 후보 선별에만 쓰고, 저장 여부는 실제 파일 해시로 결정한다.
    return bool(text_gain(old.body_text, new.body_text, old.content_profile, new.content_profile) or set(new.attachment_urls) - set(old.attachment_urls))


def enriched_subject(current, title, board):
    title = display_title(clean_display_title(title))
    if current.startswith("⭐"):
        return "⭐" + title.lstrip("⭐")
    if board in ("기타 공지사항", "장학금"):
        prefix = re.match(r"^\[[^\]]+\]\s*", current)
        if prefix and not title.startswith(prefix.group(0).strip()):
            return prefix.group(0) + title
    return title


def merged_body(current_html, announcement, old_notice):
    link = '<p>본문 링크 : <a href="{0}">{0}</a></p>'.format(
        escape(announcement.url, quote=True)
    )
    return link + sanitize_html(announcement.content_html, announcement.url)


class PublishedNoticeStore:
    def __init__(self, base_dir):
        self.root = Path(base_dir) / ".runtime/intelligence"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "published-posts.json"

    def load(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 1:
            raise PostEditError("Unsupported published index")
        return data["posts"]

    def put(self, record):
        posts = [
            row for row in self.load() if row["article_url"] != record["article_url"]
        ]
        posts.append(record)
        cutoff = (datetime.now() - timedelta(days=14)).isoformat()
        posts = [row for row in posts if row["notice"]["observed_at"] >= cutoff][-1000:]
        value = json.dumps({"schema": 1, "posts": posts}, ensure_ascii=False)
        temporary = self.path.with_suffix(".pending")
        temporary.write_text(value, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def find(self, announcement, department):
        candidate = as_notice(announcement, department)
        matches = []
        for record in self.load():
            if (
                record.get("disabled")
                or announcement.url in record["sources"]
                or announcement.url in record.get("ignored_sources", [])
            ):
                continue
            old = Notice(**record["notice"])
            if compare_notices(candidate, old)[
                "relation"
            ] == "same_event" and adds_information(old, candidate):
                matches.append(record)
        return matches[0] if len(matches) == 1 else None


def enabled():
    return os.getenv("NOTICE_ENRICHMENT_MODE", "off") == "enabled"


def find_target(announcement, department, base_dir):
    if not enabled():
        return None
    try:
        return PublishedNoticeStore(base_dir).find(announcement, department)
    except Exception as exc:
        print(f"보강 후보 조회 생략: {type(exc).__name__}", flush=True)
        return None


def register_post(service, announcement, department, receipt, base_dir):
    if (
        not enabled()
        or not isinstance(receipt, dict)
        or not isinstance(receipt.get("article_url"), str)
    ):
        return
    try:
        validate_article_url(receipt["article_url"])
        editor = PlatoPostEditor(
            service, Path(base_dir) / ".runtime/intelligence/edit-backups"
        )
        snapshot = receipt.get("_snapshot") or editor.read(receipt["article_url"])
        links = {
            node["href"]
            for node in BeautifulSoup(snapshot["body_html"], "html.parser").find_all(
                "a", href=True
            )
        }
        if (
            snapshot["subject"] != receipt.get("subject")
            or receipt.get("source_url") != announcement.url
            or announcement.url not in links
        ):
            raise PostEditError("Published article receipt mismatch")
        PublishedNoticeStore(base_dir).put(
            {
                "article_url": receipt["article_url"],
                "signature": snapshot["signature"],
                "notice": asdict(as_notice(announcement, department)),
                "board": announcement.notice_board_name,
                "sources": [announcement.url],
                "departments": [department],
                "updates": 0,
                "file_hashes": {
                    item["name"]: item["sha256"] for item in snapshot["files"]
                },
            }
        )
    except Exception as exc:
        # 게시 성공 뒤 색인 실패가 발생해도 같은 공지를 다시 쓰지 않는다.
        print(f"보강 대상 등록 생략: {type(exc).__name__}", flush=True)


def apply_enrichment(service, record, announcement, department, base_dir):
    editor = PlatoPostEditor(
        service, Path(base_dir) / ".runtime/intelligence/edit-backups"
    )
    current = editor.read(record["article_url"])
    if current["signature"] != record["signature"]:
        PublishedNoticeStore(base_dir).put(dict(record, disabled=True))
        print("직접 수정한 공지는 자동 보강을 생략합니다.", flush=True)
        return None
    old_notice = Notice(**record["notice"])
    new_notice = as_notice(announcement, department)
    if compare_notices(old_notice, new_notice)[
        "relation"
    ] != "same_event" or not adds_information(old_notice, new_notice):
        PublishedNoticeStore(base_dir).put(
            dict(
                record,
                ignored_sources=list(
                    dict.fromkeys(
                        record.get("ignored_sources", []) + [announcement.url]
                    )
                )[-10:],
            )
        )
        return False
    # 내용 해시가 같은 파일은 다시 올리지 않는다. 같은 이름의 다른 파일은 이름을 분리한다.
    known_hashes = {item["sha256"] for item in current["files"]}
    known_names = {item["name"] for item in current["files"]}
    folder_root = Path(base_dir) / ".runtime/intelligence"
    if (
        sum(Path(value).stat().st_size for value in announcement.files)
        > MAX_OPERATION_BYTES
    ):
        raise PostEditError("Enrichment attachments exceed limit")
    with tempfile.TemporaryDirectory(prefix="upload-", dir=folder_root) as temporary:
        candidates = list(announcement.files)
        # 원문에 파일로 등록된 안내 이미지도 보강 자료에 포함한다.
        for item in (getattr(announcement, "metadata", {}) or {}).get("attachment_links", []):
            if Path(item.get("name", "")).suffix.lower() in IMAGES:
                try:
                    saved = download_attachment(item["url"], item["name"], referer=announcement.url, directory=Path(temporary) / "images", allow_images=True, max_bytes=MAX_OPERATION_BYTES)
                except (RequestException, OSError, ValueError) as exc:
                    event("보강", "안내 이미지 수집 보류", level="WARN", source_url=item["url"], error=type(exc).__name__, console=False)
                    continue
                if saved:
                    candidates.append(saved["path"])
        if sum(Path(value).stat().st_size for value in candidates) > MAX_OPERATION_BYTES:
            raise PostEditError("Enrichment attachments exceed limit")
        uploads = []
        for value in candidates:
            path = Path(value)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in known_hashes:
                continue
            name = path.name
            if name in known_names:
                name = digest[:12] + "-" + name
            if name in known_names:
                raise PostEditError("Conflicting upload filename")
            destination = Path(temporary) / name
            destination.write_bytes(path.read_bytes())
            os.chmod(destination, 0o600)
            uploads.append(destination)
            known_hashes.add(digest)
            known_names.add(name)
        candidate_html = sanitize_html(announcement.content_html, announcement.url)
        improvements = body_gain(current["body_html"], candidate_html)
        if not uploads and not improvements:
            record = dict(
                record,
                sources=list(dict.fromkeys(record["sources"] + [announcement.url])),
            )
            PublishedNoticeStore(base_dir).put(record)
            return False
        # 파일만 추가되는 경우에는 기존 제목과 본문을 그대로 둔다.
        subject = current["subject"]
        body = merged_body(current["body_html"], announcement, old_notice) if improvements else current["body_html"]
        reasons = improvements + (["attachment_added"] if uploads else [])
        event("보강", "보강 자료 확인", source_url=announcement.url, reason=",".join(reasons), attachment_count=len(uploads), console=False)
        if len(body.encode()) > 300000:
            raise PostEditError("Enriched body exceeds limit")
        try:
            result = editor.update(
                record["article_url"],
                subject,
                body,
                uploads,
                expected_signature=record["signature"],
            )
        except PostEditRestoredError as exc:
            return _preserved_after_failed_enrichment(
                record, announcement, exc.restored, base_dir
            )
        if os.getenv("POST_VALIDATION_MODE", "off") == "enabled":
            service.safe_get(record["article_url"], login_required=True)
            expected_text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
            rendered = inspect_rendered_post(
                service.driver, subject, expected_text, announcement.url if improvements else old_notice.source_url
            )
            if any(
                reason != "image_load_unconfirmed" or "image_added" in improvements for reason in rendered["reasons"]
            ):
                restored = editor.restore(result)
                return _preserved_after_failed_enrichment(
                    record, announcement, restored, base_dir
                )
    record = dict(
        record,
        signature=result["after"]["signature"],
        notice=asdict(new_notice if improvements else old_notice),
        sources=list(dict.fromkeys(record["sources"] + [announcement.url])),
        departments=sorted(set(record["departments"] + [department])),
        updates=record["updates"] + 1,
        last_enrichment_reasons=reasons,
        file_hashes={item["name"]: item["sha256"] for item in result["after"]["files"]},
    )
    PublishedNoticeStore(base_dir).put(record)
    print("공지 보강 완료: 제목·본문·기존 및 새 첨부파일 검증", flush=True)
    return True
