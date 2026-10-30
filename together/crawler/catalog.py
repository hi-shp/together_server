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
