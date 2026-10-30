"""검토된 정렬 제안 CSV를 운영 source_catalog에 안전하게 반영한다.

구독 식별값(identity_department), 목록 주소, 메뉴/이름 출처와 페이지 정보는
그대로 보존한다. 실제 동작 변경은 표시 이름과 순서뿐이며, 소속 검토 결과는
향후 감사를 위한 메타데이터로 추가한다.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


KEY = ("page_url", "identity_department")
PRESERVED = (
    "page_url",
    "identity_department",
    "menu_source",
    "name_source",
    "page_index",
    "page_indexes",
)


def load_proposal(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def apply(catalog: list[dict], proposal: list[dict[str, str]]) -> list[dict]:
    if len(catalog) != len(proposal):
        raise ValueError(f"row count mismatch: catalog={len(catalog)} proposal={len(proposal)}")

    original = {(row[KEY[0]], row[KEY[1]]): row for row in catalog}
    if len(original) != len(catalog):
        raise ValueError("catalog has duplicate subscription identities")
    proposed = {(row["목록 주소"], row["기존 구독 이름"]): row for row in proposal}
    if len(proposed) != len(proposal):
        raise ValueError("proposal has duplicate subscription identities")
    if original.keys() != proposed.keys():
        missing = original.keys() - proposed.keys()
        extra = proposed.keys() - original.keys()
        raise ValueError(f"identity set mismatch: missing={len(missing)} extra={len(extra)}")

    result: list[dict] = []
    for key, before in original.items():
        reviewed = proposed[key]
        after = dict(before)
        after["order"] = int(reviewed["제안 순서"])
        after["display_name"] = reviewed["정리 후 사이트 이름"]
        after["sort_group"] = reviewed["상위 묶음"]
        after["affiliation"] = reviewed["학과·조직 묶음"]
        after["affiliation_evidence"] = reviewed["소속 근거"]
        after["affiliation_source"] = reviewed["소속 확인 주소"]
        after["affiliation_confidence"] = reviewed["소속 확실도"]
        for field in PRESERVED:
            if after.get(field) != before.get(field):
                raise AssertionError(f"preserved field changed: {field} {key}")
        result.append(after)

    result.sort(key=lambda row: row["order"])
    orders = [row["order"] for row in result]
    if orders != list(range(1, len(result) + 1)):
        raise ValueError("proposal orders are not contiguous")
    if [row["identity_department"] for row in result[:4]] != [
        "대학 공지",
        "대학 공지",
        "학생지원시스템(공지사항)",
        "학생지원시스템(장학 공지사항)",
    ]:
        raise ValueError("protected first four sources changed")
    if any(row.get("sort_group") == "28 기타 학과·전공" for row in result):
        raise ValueError("unassigned undergraduate group remains")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    proposal = load_proposal(args.proposal)
    result = apply(catalog, proposal)
    prior_orders = {
        (row["page_url"], row["identity_department"]): row["order"]
        for row in catalog
    }
    changed_order = sum(
        prior_orders[(row["page_url"], row["identity_department"])] != row["order"]
        for row in result
    )
    if args.write:
        with args.catalog.open("w", encoding="utf-8", newline="\r\n") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "rows": len(result),
        "changed_order_positions": changed_order,
        "unassigned_undergraduate_groups": sum(
            row.get("sort_group") == "28 기타 학과·전공" for row in result
        ),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
