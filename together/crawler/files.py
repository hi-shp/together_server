"""첨부파일 이름 결정과 원자적 다운로드."""

from email.header import decode_header, make_header
from email.message import Message
import hashlib
import html
from itertools import chain
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
from together.crawler import http as public_http

from together.config import HTTP_TIMEOUT

IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
          ".svg", ".ico", ".avif", ".heic", ".heif", ".jxl", ".jfif"}
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


def is_image_attachment(name="", content_type="", prefix=b""):
    """첨부의 표시 이름뿐 아니라 응답 형식과 실제 파일 머리도 확인한다."""
    if Path(sanitize_filename(name)).suffix.lower() in IMAGES:
        return True
    if content_type.split(";", 1)[0].strip().lower().startswith("image/"):
        return True
    if prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
                          b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+", b"\x00\x00\x01\x00", b"\xff\x0a",
                          b"\x00\x00\x00\x0cJXL \r\n\x87\n")):
        return True
    if len(prefix) >= 14 and prefix[:2] == b"BM" and prefix[6:10] == b"\x00" * 4 and int.from_bytes(prefix[10:14], "little") >= 14:
        return True
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return True
    if prefix[4:8] == b"ftyp" and any(
        brand in prefix[8:40] for brand in (b"avif", b"avis", b"heic", b"heix", b"heif", b"mif1")
    ):
        return True
    start = prefix.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return start.startswith(b"<svg") or (start.startswith(b"<?xml") and b"<svg" in start)


def is_image_file(path):
    with Path(path).open("rb") as handle:
        return is_image_attachment(Path(path).name, prefix=handle.read(512))


def download_attachment(url, suggested_name, *, referer, directory="downloads", max_bytes=None, http_client=None):
    initial = sanitize_filename(suggested_name)
    if is_image_attachment(initial):
        return None
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Invalid download directory")
    pending = None
    try:
        with (http_client or public_http).get(
            url,
            verify=False,
            timeout=HTTP_TIMEOUT,
            stream=True,
            headers={"Referer": referer},
        ) as response:
            response.raise_for_status()
            header_name = response_filename(response)
            content_type = response.headers.get("Content-Type", "")
            if is_image_attachment(header_name, content_type):
                return None
            name = initial or header_name
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
            if is_image_attachment(name):
                return None
            if "text/html" in response.headers.get("Content-Type", "").lower() and Path(
                name
            ).suffix.lower() not in (".html", ".htm"):
                raise ValueError("Attachment endpoint returned HTML")
            chunks = iter(response.iter_content(1024 * 1024))
            buffered = []
            prefix = b""
            for chunk in chunks:
                if chunk:
                    buffered.append(chunk)
                    prefix += chunk[:512 - len(prefix)]
                    if len(prefix) >= 512:
                        break
            if is_image_attachment(name, content_type, prefix):
                return None
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".download-", delete=False
            ) as handle:
                pending = Path(handle.name)
                os.chmod(pending, 0o600)
                for chunk in chain(buffered, chunks):
                    if not chunk:
                        continue
                    if shutil.disk_usage(root).free - len(chunk) < 1024**3:
                        raise OSError("Download free-space floor reached")
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("Attachment exceeds operation limit")
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
