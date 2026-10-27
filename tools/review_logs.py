"""공지별 진행 이력과 오류를 조회하거나 검토 자료로 내보낸다."""

import argparse
from collections import deque
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.config import BASE_DIR


def read_events(root, *, notice_id=None, errors=False, limit=50):
    selected = deque(maxlen=limit)
    for path in sorted(root.glob("events.jsonl*"), key=lambda p: p.stat().st_mtime):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if notice_id and record.get("notice_id") != notice_id:
                    continue
                if errors and record.get("level") not in ("WARN", "ERROR"):
                    continue
                selected.append(record)
    return list(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notice-id")
    parser.add_argument("--errors", action="store_true")
    parser.add_argument("--tail", type=int, default=50)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    if not 1 <= args.tail <= 10000:
        parser.error("--tail은 1~10000 범위입니다.")
    records = read_events(
        BASE_DIR / ".runtime/telemetry",
        notice_id=args.notice_id,
        errors=args.errors,
        limit=args.tail,
    )
    if args.export:
        with args.export.open("x", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        args.export.chmod(0o600)
        print(f"검토 자료 {len(records)}건 저장: {args.export}")
    else:
        for record in records:
            print(
                f"{record['time']} {record['level']:5} [{record.get('notice_id','운영')}] {record['stage']} | {record['message']}"
            )


if __name__ == "__main__":
    main()
