"""첨부파일 이름 결정과 원자적 다운로드."""

from email.header import decode_header, make_header
from email.message import Message
import hashlib
import html
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
import requests

from together.config import HTTP_TIMEOUT

IMAGES = {".png", ".jpg", ".jpeg", ".gif"}
GENERIC = re.compile(
    r"^(?:다운로드|첨부파일|파일|download|attachment|downloadrun\.do|download\.do|download\.(?:asp|php))$",
    re.I,
)


def sanitize_filename(value):
    value = unicodedata.normalize("NFC", html.unescape(str(value or "")))
    value = "".join(
        c for c in value if unicodedata.category(c) not in ("Cc", "Cf", "Cs")
    )
    value = re.sub(r"\s*\([\d.,]+\s*(?:[KMGT]?i?B|바이트)\)\s*$", "", value, flags=re.I)
    value = re.sub(r'[\\/:*?"<>|]', "_", value).strip(" .")
    if not value or GENERIC.fullmatch(value):
        return ""
    if re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "_" + value
    suffix = Path(value).suffix
    if len(suffix.encode()) > 32:
        suffix = ""
    stem = value[: -len(suffix)] if suffix else value
    stem = stem.encode("utf-8")[: 210 - len(suffix.encode("utf-8"))].decode(
        "utf-8", errors="ignore"
    )
    return stem + suffix


def response_filename(response):
    header = Message()
    header["content-disposition"] = response.headers.get("Content-Disposition", "")
    name = header.get_filename() or ""
    try:
        name = str(make_header(decode_header(name)))
    except (UnicodeError, LookupError):
        pass
    return sanitize_filename(name)


def download_attachment(url, suggested_name, *, referer, directory="downloads"):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Invalid download directory")
    initial = sanitize_filename(suggested_name)
    if Path(initial).suffix.lower() in IMAGES:
        return None
    pending = None
    try:
        with requests.get(
            url,
            verify=False,
            timeout=HTTP_TIMEOUT,
            stream=True,
            headers={"Referer": referer},
        ) as response:
            response.raise_for_status()
            name = initial or response_filename(response)
            if not name:
                path_name = sanitize_filename(
                    unquote(Path(urlsplit(response.url).path).name)
                )
                if Path(path_name).suffix.lower() not in (
                    ".do",
                    ".php",
                    ".asp",
                    ".html",
                    "",
                ):
                    name = path_name
            if not name:
                extensions = {
                    "application/pdf": ".pdf",
                    "application/x-hwp": ".hwp",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                }
                extension = extensions.get(
                    response.headers.get("Content-Type", "").split(";")[0].lower(),
                    ".bin",
                )
                name = (
                    "attachment-"
                    + hashlib.sha256(url.encode()).hexdigest()[:12]
                    + extension
                )
            if Path(name).suffix.lower() in IMAGES:
                return None
            if "text/html" in response.headers.get("Content-Type", "").lower() and Path(
                name
            ).suffix.lower() not in (".html", ".htm"):
                raise ValueError("Attachment endpoint returned HTML")
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".download-", delete=False
            ) as handle:
                pending = Path(handle.name)
                os.chmod(pending, 0o600)
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    if shutil.disk_usage(root).free - len(chunk) < 1024**3:
                        raise OSError("Download free-space floor reached")
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            sha = digest.hexdigest()
            candidate = root / name
            from together.storage.attachments import file_digest

            for index in range(100):
                if not candidate.exists() and not candidate.is_symlink():
                    break
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and file_digest(candidate) == sha
                ):
                    pending.unlink()
                    pending = None
                    return {
                        "path": str(candidate),
                        "filename": candidate.name,
                        "bytes": size,
                        "sha256": sha,
                    }
                candidate = root / f"{sha[:12]}-{index+1}-{name}"
            else:
                raise FileExistsError("Attachment filename collision limit")
            os.replace(pending, candidate)
            pending = None
            return {
                "path": str(candidate),
                "filename": candidate.name,
                "bytes": size,
                "sha256": sha,
            }
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
