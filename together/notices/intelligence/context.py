"""판단 당시 보관한 원문에서 중복 검토에 필요한 본문을 읽는다."""

from datetime import timedelta
from pathlib import Path
import re
import sqlite3


def department_conditions_differ(context):
    """같은 현장실습의 학과별 추가 조건을 공통 안내에 덮어 합치지 않는다."""
    notices = [context.get("new_notice", {}), *context.get("recent_notices", [])]
    scopes = []
    for notice in notices:
        body = re.sub(r"\s+", "", notice.get("body_excerpt", ""))
        department = re.sub(r"\([^)]*\)", "", notice.get("department", ""))
        department = re.sub(r"\s+", "", department)
        conditions = set()
        if len(department) >= 3 and re.search(r"학과|학부|전공", department):
            for match in re.finditer(re.escape(department) + r".{0,100}", body):
                if re.search(r"학점|제출|지원자격|참여대상|학년|이수|인정|불가", match[0]):
                    conditions.add(match[0])
        scopes.append(conditions)
    return len(scopes) > 1 and any(scopes[0] != other and (scopes[0] or other) for other in scopes[1:])


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
