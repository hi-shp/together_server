"""공식 표시 이름·정렬을 저장된 구독 이름과 분리한다.

CSV의 department/page_url/number는 기존 식별자로 유지한다. 표시 이름 변경이
구독자 선택이나 발송 이벤트 키, 대학 공지의 기록 경계를 바꾸지 않게 한다.
"""

from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def records():
    path = Path(__file__).with_name("source_catalog.json")
    return {
        (row["page_url"], row["identity_department"]): row
        for row in json.loads(path.read_text(encoding="utf-8"))
    }


def display_name(department, source_url):
    row = records().get((source_url, department))
    return row["display_name"] if row else " ".join(department.split())


def order_pages(pages):
    catalog = records()
    # 새로 등록한 행도 빠뜨리지 않고 CSV 순서대로 뒤에 둔다.
    return sorted(pages, key=lambda page: catalog.get(
        (page.source_url, page.department), {}
    ).get("order", len(catalog) + 1))


def coalesce_pages(pages):
    """같은 실제 게시판은 한 번만 읽되 기존 구독 이름은 모두 보존한다."""
    catalog = records()
    merged = []
    by_board = {}
    for page in pages:
        row = catalog.get((page.source_url, page.department), {})
        board = row.get("canonical_board") or page.source_url
        current = by_board.get(board)
        if current is None:
            page.notification_departments = [page.department]
            page.source_urls = [page.source_url]
            by_board[board] = page
            merged.append(page)
            continue
        if page.department not in current.notification_departments:
            current.notification_departments.append(page.department)
        if page.source_url not in current.source_urls:
            current.source_urls.append(page.source_url)
        # 어느 별칭의 기록이 뒤처졌더라도 새 공지를 놓치지 않는다.
        current.number = min(current.number, page.number)
    return merged


def set_display_ordinals(pages):
    """대학 공지의 내부 두 목록은 유지하고 표시 순번만 하나로 묶는다."""
    from urllib.parse import parse_qs, urlsplit
    ordinal = 0
    university_seen = False
    for page in pages:
        parsed = urlsplit(page.source_url)
        university = parsed.hostname == "www.pusan.ac.kr" and parsed.path == "/kor/CMS/Board/PopupBoard.do" and parse_qs(parsed.query).get("mgr_seq") == ["3"]
        if not (university and university_seen):
            ordinal += 1
        page.display_ordinal = ordinal
        university_seen = university_seen or university
