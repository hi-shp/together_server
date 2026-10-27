"""판단 당시 보관한 원문에서 중복 검토에 필요한 본문을 읽는다."""

from datetime import timedelta
from pathlib import Path
import sqlite3


def excerpt(text):
    text = str(text or "")
    return text if len(text) <= 1800 else text[:1200] + "\n…\n" + text[-600:]


def duplicate_context(announcement, department, candidates, dates, base_dir):
    body = getattr(announcement, "content_text", "") or ""
    path = Path(base_dir) / ".runtime/intelligence/observations.sqlite3"
    if len(body.strip()) < 80 or not path.exists():
        return None
    previous = []
    try:
        with sqlite3.connect(
            "file:" + str(path) + "?mode=ro", uri=True, timeout=0.2
        ) as db:
            for title in candidates:
                stamp = dates.get(title)
                if stamp is None:
                    previous.append({"title": title})
                    continue
                row = db.execute(
                    "SELECT source_url,department,body_excerpt FROM notices WHERE title=? AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1",
                    (
                        title,
                        (stamp + timedelta(seconds=5)).isoformat(timespec="seconds"),
                    ),
                ).fetchone()
                previous.append(
                    {
                        "title": title,
                        "source_url": row[0],
                        "department": row[1],
                        "body_excerpt": excerpt(row[2]),
                    }
                    if row
                    else {"title": title}
                )
    except (sqlite3.Error, OSError):
        return None
    if not any(len(row.get("body_excerpt", "").strip()) >= 80 for row in previous):
        return None
    return {
        "new_notice": {
            "title": getattr(announcement, "original_title", announcement.title),
            "department": department,
            "source_url": announcement.url,
            "body_excerpt": excerpt(body),
        },
        "recent_notices": previous,
    }
