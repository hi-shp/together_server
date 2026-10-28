"""확인된 복구·신규 게시판을 기존 CSV의 끝에 한 번만 추가한다."""

import csv
import json
import os
from pathlib import Path


def restore_boards(root):
    root = Path(root)
    config = json.loads((root / "together/crawler/recovered_boards.json").read_text(encoding="utf-8"))
    additional = root / "together/crawler/additional_boards.json"
    if additional.exists():
        config += json.loads(additional.read_text(encoding="utf-8"))
    from urllib.parse import urlsplit
    for item in config:
        assert isinstance(item.get("department"), str) and item["department"].strip(), "Missing board name"
        parts = urlsplit(item.get("page_url", ""))
        assert parts.scheme in ("http", "https") and parts.hostname, "Invalid board URL"
        assert type(item.get("number")) is int and item["number"] >= 0, "Invalid board baseline"
    path = root / (os.getenv("PAGE_NAME") or "pages.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    assert fields == ["department", "page_url", "number"], "Unexpected CSV columns"
    added = []
    for item in config:
        if any(row["department"] == item["department"] or row["page_url"] in (item["page_url"], item.get("previous_url", item["page_url"])) for row in rows):
            continue
        rows.append({key: str(item[key]) for key in fields})
        added.append(item["department"])
    if added:
        # 기존 행과 번호는 그대로 보관하고 새 행만 덧붙인다.
        before = path.read_bytes()
        suffix = "" if before.endswith(b"\n") else "\n"
        import io
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writerows(rows[-len(added):])
        pending = path.with_suffix(".restore-pending")
        pending.write_bytes(before + suffix.encode() + buffer.getvalue().encode("utf-8"))
        os.replace(pending, path)
    return added


if __name__ == "__main__":
    print(json.dumps({"restored_boards": restore_boards(Path(__file__).resolve().parents[1])}, ensure_ascii=False))
