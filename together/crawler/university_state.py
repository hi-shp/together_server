"""대학 공지의 제목 기준과 실패 시 재시작 위치를 보존한다."""

import csv
from datetime import date, datetime
from functools import lru_cache
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit


def title_key(value):
    return " ".join(html.unescape(str(value)).split())


@lru_cache(maxsize=2)
def _title_history(root, title_stamp, csv_stamp):
    root = Path(root)
    titles = {}
    path = root / "titles.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "$" in line:
                stamp, title = line.split("$", 1)
                try:
                    saved = date.fromisoformat(stamp[:10])
                except ValueError:
                    continue
                titles.setdefault(title_key(title), []).append(saved)
    return titles


def known_title(root, title, publication_date=None):
    root = Path(root)
    stamp = lambda name: (
        (root / name).stat().st_mtime_ns if (root / name).exists() else 0
    )
    titles = _title_history(
        str(root), stamp("titles.txt"), stamp("sorted_labeled_data.csv")
    )
    key = title_key(title)
    try:
        published = (
            date.fromisoformat(publication_date)
            if publication_date
            else datetime.now().date()
        )
    except ValueError:
        published = datetime.now().date()
    horizon = 45 if publication_date else 10
    matches = lambda values: any(
        -2 <= (saved - published).days <= horizon for saved in values
    )
    if key in titles and matches(titles[key]):
        return True
    if key.endswith("..."):
        prefix = re.sub(r"(?:&[^; ]*)?\.\.\.$", "", key).rstrip()
        return len(prefix) > 20 and any(
            value.startswith(prefix) and matches(dates)
            for value, dates in titles.items()
        )
    return False


def article_id(url):
    return parse_qs(urlsplit(url).query).get("board_seq", [""])[0]


def receipt_for(root, url):
    path = Path(root) / ".runtime/published-receipts.json"
    if not path.exists():
        return None
    ident = article_id(url)
    return next(
        (
            row
            for row in json.loads(path.read_text()).values()
            if ident and article_id(row.get("source_url", "")) == ident
        ),
        None,
    )


def pinned_candidate(root, url, title, publication_date=None):
    receipt = receipt_for(root, url)
    if receipt:
        return (
            None if receipt.get("notifications_registered") else receipt["source_url"]
        )
    return None if known_title(root, title, publication_date) else url


class UniversityCheckpoint:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / ".runtime/university-checkpoint.json"
        self.state = None

    def _restore(self, state):
        path = self.root / "yesterday.txt"
        pending = path.with_suffix(".checkpoint-pending")
        pending.write_text(state["yesterday"], encoding="utf-8")
        os.replace(pending, path)
        target = self.root / state["csv_file"]
        with target.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        for row in rows[1:]:
            if row[1] in state["numbers"]:
                row[2] = state["numbers"][row[1]]
        pending = target.with_suffix(".checkpoint-pending")
        with pending.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
        os.replace(pending, target)

    def __enter__(self):
        target = self.root / "yesterday.txt"
        csv_file = os.getenv("PAGE_NAME") or "pages.csv"
        if not target.exists() or not (self.root / csv_file).exists():
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists():
            self._restore(json.loads(self.path.read_text()))
            self.path.unlink()
        with (self.root / csv_file).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.state = {
            "yesterday": target.read_text(encoding="utf-8"),
            "csv_file": csv_file,
            "numbers": {
                row[1]: row[2]
                for row in rows[1:]
                if "CMS/Board/PopupBoard.do" in row[1]
            },
        }
        pending = self.path.with_suffix(".pending")
        pending.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        pending.chmod(0o600)
        os.replace(pending, self.path)
        return self

    def __exit__(self, kind, value, traceback):
        if self.state is not None and self.path.exists():
            if kind is not None:
                self._restore(self.state)
            self.path.unlink()
