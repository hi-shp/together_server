"""이미 받은 HTML에서 원문·첨부파일·명시된 게시일을 읽는다."""

import re
from datetime import date
from urllib.parse import urljoin, urlsplit


def _date(value):
    match = re.search(r"((?:19|20)\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", value)
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def extract_metadata(soup, source_url):
    attachments = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not re.search(
            r"download\.do|downloadRun\.do|download\.(?:asp|php)", href, re.I
        ):
            continue
        if "preview" in link.get("class", []):
            continue
        url = urljoin(source_url, href)
        if urlsplit(url).scheme in ("http", "https"):
            attachments[url] = {"url": url, "name": link.get_text(" ", strip=True)}
    publication_time = None
    publication_time_source = None
    for node in soup.select(
        'time[datetime], meta[property="article:published_time"], .artclWriteDate'
    ):
        value = (
            node.get("datetime")
            or node.get("content")
            or node.get_text(" ", strip=True)
        )
        publication_time = _date(value)
        if publication_time:
            publication_time_source = 'explicit_date_element'
            break
    if publication_time is None:
        for node in soup.find_all("dt"):
            if re.fullmatch(
                r"(?:작성|등록|게시)\s*일(?:자)?", node.get_text(strip=True)
            ):
                value = node.find_next_sibling("dd")
                if value:
                    publication_time = _date(value.get_text())
                    if publication_time:
                        publication_time_source = 'explicit_definition_list_label'
                        break
    if publication_time is None:
        # 실제 PNU 상세 페이지의 제목 바로 아래 정보 영역만 읽는다.
        # 본문의 행사일·마감일이나 '게시종료일'은 원래 작성일이 아니다.
        for node in soup.select('.board-view .title ul.detail li, .vtitle-winfo .txt'):
            value = node.get_text(' ', strip=True)
            match = re.match(r'^(?:작성|등록|게시)\s*일(?:자)?\s*[:：]?\s*(.+)$', value)
            if match:
                publication_time = _date(match[1])
                if publication_time:
                    publication_time_source = 'explicit_article_header_label'
                    break
    attachment_region = soup.select_one(
        "dd.artclInsert, .board-winfo-files, dl.half-box01, div.attachment"
    )
    return {
        "source_url": source_url,
        "attachment_links": list(attachments.values()),
        "attachment_scan_complete": bool(attachments or attachment_region),
        "inline_image_count": len(soup.find_all("img")),
        "publication_time": publication_time,
        "publication_time_is_explicit": publication_time is not None,
        "publication_time_source": publication_time_source,
    }
