"""학교 대표 홈페이지의 학술행사·채용 게시판."""

from urllib.parse import parse_qs, urljoin, urlsplit

BOARD_MANAGERS = {"MN077": "2", "MN103": "10"}


def manager_for(url):
    parts = urlsplit(url)
    if parts.hostname != "www.pusan.ac.kr" or parts.path != "/kor/CMS/Board/Board.do":
        return None
    return BOARD_MANAGERS.get(parse_qs(parts.query).get("mCode", [""])[0])


def collect(page, soup, response_url, ordinal, event, record_board_result):
    manager = manager_for(page.page_url)
    table = soup.find("tbody")
    if table is None:
        record_board_result(False)
        event("수집", f"공지 목록을 확인할 수 없습니다: {page.page_url}", level="WARN")
        return [], page.number
    articles = {}
    for row in table.select("tr"):
        link = row.select_one("td.subject a[href]")
        if link is None:
            continue
        url = urljoin(response_url, link["href"])
        query = parse_qs(urlsplit(url).query)
        identifier = query.get("board_seq", [""])[0]
        if query.get("mgr_seq", [""])[0] == manager and identifier.isdigit():
            articles[int(identifier)] = url
    if not articles and not any(text in table.get_text(" ", strip=True) for text in ("게시물이 없습니다", "게시물이(가) 없습니다", "등록된 게시물이 없습니다")):
        record_board_result(False)
        event("수집", f"공지 링크를 확인할 수 없습니다; 번호 유지: {page.page_url}", level="WARN")
        return [], page.number
    record_board_result(True)
    maximum = max([page.number, *articles])
    selected = [articles[key] for key in sorted(articles) if key > page.number]
    event("수집", f"{ordinal}. {page.department} {maximum}", plain=True)
    event("수집", f"💡{len(selected)}개의 New Announcment!: {page.page_url}" if selected else f"새로 추가된 공지사항 없음: {page.page_url}", plain=True)
    return selected, maximum
