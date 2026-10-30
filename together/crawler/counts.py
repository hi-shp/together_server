"""표시용 게시물 수. 수집 경계인 글 ID와는 독립적으로 계산한다."""

import re
from urllib.parse import parse_qs, urlsplit


def notice_count(soup, visible_count, last_page_count=None):
    for node in soup.select(".board-total .total, .board_total, .artclTotal, ._total"):
        match = re.search(r"(?:전체|총)\s*[:：]?\s*([\d,]+)\s*(?:건|개)?", node.get_text(" ", strip=True))
        if match:
            return int(match[1].replace(",", ""))
    pages = []
    for node in soup.select("._paging ._totPage"):
        text = node.get_text(strip=True).replace(",", "")
        if text.isdigit():
            pages.append(int(text))
    for node in soup.select("._paging a._last, .paging a.last, .pagination a.last"):
        href = node.get("href", "")
        match = re.search(r"page_link\(['\"]?(\d+)", href)
        if match:
            pages.append(int(match[1]))
        else:
            value = parse_qs(urlsplit(href).query).get("page", [""])[0]
            if value.isdigit():
                pages.append(int(value))
    if pages and max(pages) > 1:
        last = max(pages)
        remaining = last_page_count(last) if last_page_count else None
        if remaining is not None and 0 <= remaining <= visible_count:
            return (last - 1) * visible_count + remaining
        return last * visible_count
    return visible_count
