"""Recover a K2Web module failure without guessing a different board."""
import re
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup


def direct_list_url(source_url, soup):
    parsed = urlsplit(source_url)
    if parsed.scheme not in {"http", "https"} or not (parsed.hostname or "").endswith(".pusan.ac.kr"):
        return None
    path = re.fullmatch(r"/([A-Za-z0-9_-]+)/\d+/subview\.do", parsed.path)
    modules = set(re.findall(r"fnctId=bbs,\s*fnctNo=(\d+)", str(soup)))
    if not path or len(modules) != 1 or soup.find("tbody") is not None:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/bbs/{path[1]}/{modules.pop()}/artclList.do"


def recover_list(source_url, soup, fetch):
    target = direct_list_url(source_url, soup)
    if not target:
        return None
    response = fetch(target, isolated=True)
    # A redirect to another board/login/home must not become an empty success.
    if urlsplit(response.url)._replace(query="", fragment="") != urlsplit(target):
        return None
    candidate = BeautifulSoup(response.text, "html.parser")
    if not any(urlsplit(urljoin(target, f.get("action", ""))).path == urlsplit(target).path
               for f in candidate.find_all("form") if f.get("action")):
        return None
    table = candidate.find("tbody")
    if table is None:
        return None
    board_path = urlsplit(target).path.rsplit("/", 1)[0] + "/"
    links = table.select("td._artclTdTitle a[href]")
    valid_links = bool(links) and all(
        urlsplit(urljoin(target, a["href"])).netloc == urlsplit(target).netloc
        and re.fullmatch(re.escape(board_path) + r"\d+/artclView\.do", urlsplit(urljoin(target, a["href"])).path)
        for a in links
    )
    empty = candidate.select_one("th._artclThNum") is not None and any(
        text in table.get_text(" ", strip=True)
        for text in ("게시물이(가) 없습니다", "there is no a posting")
    )
    return (response, candidate) if valid_links or empty else None
