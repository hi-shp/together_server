"""번호 칸이 없는 교내 게시판을 글 식별자로 확인한다."""

import base64
import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit


@lru_cache(maxsize=1)
def configured_sources():
    path = Path(__file__).with_name("additional_boards.json")
    return {row["page_url"]: row["canonical_board"] for row in json.loads(path.read_text(encoding="utf-8")) if row.get("parser") == "k2_article_id"}


def configured_board(url):
    return configured_sources().get(url)


def entries(soup, response_url, expected):
    forms = set()
    for form in soup.select("form[action]"):
        match = re.search(r"/bbs/([^/]+)/(\d+)/artclList\.do", form["action"])
        if match:
            forms.add(f"k2web:{match[1].lower()}:{match[2]}")
    if forms != {expected}:
        return None
    articles = {}
    for link in soup.select("a[href]"):
        target = urljoin(response_url, link["href"])
        hostname = urlsplit(target).hostname or ""
        if not (hostname.endswith(".pusan.ac.kr") or hostname.endswith(".busan.ac.kr")):
            continue
        encoded = parse_qs(urlsplit(target).query).get("enc")
        path = urlsplit(target).path
        if encoded:
            try:
                value = encoded[0].replace(" ", "+")
                path = unquote(base64.b64decode(value + "=" * (-len(value) % 4)).decode()).split("|@@|")[-1]
            except (ValueError, UnicodeError):
                continue
        match = re.search(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do(?:$|[?])", path)
        if match and f"k2web:{match[1].lower()}:{match[2]}" == expected:
            articles[int(match[3])] = target
    return articles


def collect(page, soup, response_url, expected, ordinal, event, record_board_result):
    articles = entries(soup, response_url, expected)
    if articles is None:
        record_board_result(False)
        event("수집", f"게시판 형식 확인 실패; 번호 유지: {page.page_url}", level="WARN")
        return [], page.number
    if not articles and not any(text in soup.get_text(" ", strip=True) for text in ("게시물이(가) 없습니다", "게시물이 없습니다", "there is no a posting")):
        record_board_result(False)
        event("수집", f"공지 링크 확인 필요; 번호 유지: {page.page_url}", level="WARN")
        return [], page.number
    record_board_result(True)
    maximum = max([page.number, *articles])
    selected = [articles[key] for key in sorted(articles) if key > page.number]
    event("수집", f"{ordinal}. {page.department} {maximum}", plain=True)
    event("수집", f"💡{len(selected)}개의 New Announcment!: {page.page_url}" if selected else f"새로 추가된 공지사항 없음: {page.page_url}", plain=True)
    return selected, maximum
