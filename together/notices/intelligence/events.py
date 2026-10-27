"""행사 회차 충돌을 먼저 확인하고 대표 원문 후보를 비교한다."""

from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
import re
from urllib.parse import urlsplit, urlunsplit

from .text import clean_display_title, exact_key, identity, normalize_title


@dataclass
class Notice:
    title: str
    observed_at: str = ""
    source_url: str = ""
    department: str = ""
    body_text: str = ""
    attachment_urls: list = field(default_factory=list)
    metadata_complete: bool = False
    publication_time: str = ""
    content_profile: dict = field(default_factory=dict)


def phase(title):
    title = normalize_title(title)
    if re.search(
        r"선발\s*결과|선정\s*결과|합격자|수상자|결과\s*(?:공고|발표)|수상\s*안내", title
    ):
        return "result"
    if re.search(r"모집|신청|접수|참가자|참가팀|수강생", title):
        return "recruitment"
    return "information"


def _core(title):
    title = clean_display_title(title)
    # 게시 부서와 원문 기관의 구분은 별도 메타데이터로 남긴다.
    title = re.sub(r"^(?:\[[^\]]{1,80}\]\s*)+", "", title)
    title = re.sub(
        r"(?:안내|홍보\s*협조\s*요청|재공지|재안내|공고|모집|신청)", "", title
    )
    return "".join(char.lower() for char in title if char.isalnum())


def _url(value):
    parts = urlsplit(value)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


def _rounds(values, unit):
    return {value for value in values if value.endswith(unit)}


def compare_notices(left, right):
    a, b = identity(left.title), identity(right.title)
    reasons = []
    for name in ("years", "semesters", "seasons"):
        if a[name] and b[name] and not set(a[name]) & set(b[name]):
            return {
                "relation": "different_occurrence",
                "reason": f"{name}_conflict",
                "similarity": None,
            }
    for unit in ("회", "기", "차"):
        x, y = _rounds(a["rounds"], unit), _rounds(b["rounds"], unit)
        if x and y and not x & y:
            return {
                "relation": "different_occurrence",
                "reason": f"round_{unit}_conflict",
                "similarity": None,
            }
    first, second = _core(left.title), _core(right.title)
    similarity = SequenceMatcher(None, first, second).ratio()
    if phase(left.title) != phase(right.title) and "result" in (
        phase(left.title),
        phase(right.title),
    ):
        return {
            "relation": "related_update" if similarity >= 0.6 else "different_event",
            "reason": "result_and_recruitment_are_not_interchangeable",
            "similarity": similarity,
        }
    try:
        gap = (
            abs(
                (
                    datetime.fromisoformat(left.observed_at)
                    - datetime.fromisoformat(right.observed_at)
                ).total_seconds()
            )
            / 86400
        )
    except (TypeError, ValueError):
        gap = None
    if gap is not None and gap > 30:
        return {
            "relation": "needs_review",
            "reason": "observation_window_exceeded",
            "similarity": similarity,
        }
    if gap is None:
        reasons.append("observation_time_missing")
    if a["years"] != b["years"] and a["years"] and b["years"]:
        return {
            "relation": "needs_review",
            "reason": "partly_overlapping_years",
            "similarity": similarity,
        }
    if re.search(
        r"계절(?:학기|수업).*교류\s*수학|수강신청|수강정정|졸업논문|학위청구|지도교수|학점인정|졸업사정",
        left.title + right.title,
    ):
        if left.department and right.department and left.department != right.department:
            return {
                "relation": "needs_review",
                "reason": "department_specific_scope",
                "similarity": similarity,
            }
    if bool(a["years"]) != bool(b["years"]):
        return {
            "relation": "possible_duplicate",
            "reason": "event_year_missing_on_one_side",
            "similarity": similarity,
        }
    deadline_dates = []
    for notice in (left, right):
        dates = set()
        if re.search(r"~|∼|마감|까지", notice.title):
            dates = {
                (int(month), int(day))
                for month, day in re.findall(
                    r"(?<!\d)(\d{1,2})[./월]\s*(\d{1,2})(?!\d)", notice.title
                )
                if 1 <= int(month) <= 12 and 1 <= int(day) <= 31
            }
        deadline_dates.append(dates)
    if (
        all(deadline_dates)
        and deadline_dates[0] != deadline_dates[1]
        and similarity >= 0.72
    ):
        return {
            "relation": "related_update",
            "reason": "deadline_or_schedule_changed",
            "similarity": similarity,
        }
    if (
        left.source_url
        and right.source_url
        and _url(left.source_url) == _url(right.source_url)
        and gap is not None
    ):
        return {
            "relation": "same_event",
            "reason": "same_source_url_and_compatible_occurrence",
            "similarity": similarity,
        }
    attachments = set(left.attachment_urls) & set(right.attachment_urls)
    if attachments and similarity >= 0.75 and gap is not None:
        return {
            "relation": "same_event",
            "reason": "shared_attachment_and_event_core",
            "similarity": similarity,
        }
    prefixes = [
        re.findall(r"^\[([^\]]+)\]", clean_display_title(notice.title))
        for notice in (left, right)
    ]
    if any(prefixes) and prefixes[0] != prefixes[1]:
        return {
            "relation": "needs_review",
            "reason": "publisher_or_organizer_requires_evidence",
            "similarity": similarity,
        }
    if re.search(
        r"계절(?:학기|수업).*교류\s*수학|장학|조교|수강신청|수강정정|졸업논문|학위청구|지도교수|학점인정|졸업사정",
        left.title + right.title,
    ):
        if (
            not left.department
            or not right.department
            or left.department != right.department
        ):
            return {
                "relation": "needs_review",
                "reason": "department_scope_not_confirmed",
                "similarity": similarity,
            }
    if first == second and len(first) >= 16 and gap is not None and gap <= 14:
        has_round = bool(a["rounds"] and b["rounds"])
        has_year = bool(a["years"] and b["years"])
        if has_round or has_year:
            return {
                "relation": "same_event",
                "reason": "same_specific_core_and_occurrence",
                "similarity": similarity,
            }
    if similarity >= 0.72:
        return {
            "relation": "possible_duplicate",
            "reason": "|".join(reasons + ["title_similarity_requires_evidence"]),
            "similarity": similarity,
        }
    return {
        "relation": "different_event",
        "reason": "distinct_event_core",
        "similarity": similarity,
    }


def quality_details(notice):
    text = re.sub(r"\s+", "", notice.body_text)
    fields = {
        "schedule": bool(
            re.search(r"일시|일정|행사일|교육기간|모집기간|접수기간", text)
        ),
        "eligibility": bool(
            re.search(r"참가대상|지원자격|신청대상|모집대상|참여대상", text)
        ),
        "application": bool(
            re.search(r"신청방법|접수방법|지원방법|제출서류|신청링크", text)
        ),
        "location": bool(re.search(r"장소|온라인|오프라인|비대면", text)),
    }
    return {
        "verified_metadata": notice.metadata_complete,
        "information_fields": fields,
        "information_field_count": sum(fields.values()),
        "attachment_count": len(set(notice.attachment_urls)),
        "body_characters": len(text.strip()),
        "display_title": clean_display_title(notice.title),
        "observed_at": notice.observed_at,
    }


def select_representative(notices):
    if not notices:
        raise ValueError("No notice candidates")

    def rank(notice):
        info = quality_details(notice)
        try:
            observed = datetime.fromisoformat(notice.observed_at).timestamp()
        except (TypeError, ValueError):
            observed = float("inf")
        return (
            info["verified_metadata"],
            info["information_field_count"],
            bool(info["attachment_count"]),
            min(info["body_characters"], 3000),
            -observed,
        )

    winner = max(notices, key=rank)
    return {
        "winner": winner,
        "quality": quality_details(winner),
        "departments": sorted(
            {notice.department for notice in notices if notice.department}
        ),
        "source_urls": sorted(
            {notice.source_url for notice in notices if notice.source_url}
        ),
        "historical_quality_verifiable": all(
            notice.metadata_complete for notice in notices
        ),
    }


def group_candidates(notices):
    groups = []
    for notice in notices:
        for group in groups:
            # 유사한 제목의 연결만으로 서로 다른 회차를 합치지 않는다.
            if all(
                compare_notices(notice, member)["relation"] == "same_event"
                for member in group
            ):
                group.append(notice)
                break
        else:
            groups.append([notice])
    return groups
