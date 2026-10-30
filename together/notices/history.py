import os
import re
from datetime import datetime, timedelta
import tiktoken
from together.integrations.openai_client import check_title_similarity
from difflib import SequenceMatcher
import csv
import heapq
from collections import defaultdict, deque
from together.notices.intelligence.text import occurrence_conflict, normalize_title, requires_department_evidence, clean_display_title, identity
from together.notices.intelligence.events import Notice, compare_notices
from together.config import BASE_DIR
from together.notices.intelligence.context import duplicate_context
from together.operations.events import event, duplicate_review, duplicate_review_reason

_TITLE_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


def _parse_title_timestamp(value):
    # 저장 형식이 일치할 때만 빠르게 해석하고, 그 밖에는 기존 방식을 쓴다.
    if _TITLE_TIMESTAMP.fullmatch(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def remove_brackets(text):
    # 채용 제목은 대괄호 안의 구분 정보를 남긴다.
    if "신입" in text or "채용" in text:
        text = re.sub(r'\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]', "", text).strip()
    else:
        text = re.sub(
            r'\[.*?\]|\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]', "", text
        ).strip()

    return text


def comparison_title(title, mode="hybrid"):
    if mode != "legacy":
        # 행사명이 대괄호 안에 있고 바깥에는 '모집 안내'만 있는 경우도 보존한다.
        return "".join(char.casefold() for char in clean_display_title(title) if char.isalnum())
    cleaned = remove_brackets(title).strip()
    if not cleaned and mode != "legacy":
        # 제목 전체를 감싼 대괄호에서는 안쪽의 행사명을 남긴다.
        cleaned = remove_brackets(re.sub(r"[\[\]]", "", title)).strip()
        if not cleaned:
            cleaned = "".join(char for char in str(title) if char.isalnum())
    return cleaned


def candidate_title_similarities(new_title, titles, mode="hybrid"):
    cleaned = [comparison_title(title, mode) for title in titles]
    query = comparison_title(new_title, mode)
    if mode == "legacy":
        return _top_title_similarities(query, cleaned)
    # 긴 홍보 문구 때문에 같은 고유 프로그램의 같은 기수가 후보에서 밀리지 않게 한다.
    # 이 가산점은 후보 순서에만 쓰며 중복 확정은 기존 본문 검토가 맡는다.
    generic = {"pnu", "ai", "sw", "it", "bk21", "sns", "csr", "pbl"}
    identifiers = lambda text: {x.casefold() for x in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)} - generic
    current_ids = identifiers(new_title)
    rounds = set(identity(new_title)["rounds"])
    scored = []
    for index, (original, value) in enumerate(zip(titles, cleaned)):
        if not value:
            continue
        score = calculate_similarity(query, value)
        if current_ids & identifiers(original) and rounds & set(identity(original)["rounds"]):
            score += 0.35
        scored.append((score, -index, value))
    return [(score, title) for score, _, title in heapq.nlargest(3, scored)]


def truncate_text(text, max_tokens):
    encoding = tiktoken.get_encoding("cl100k_base")

    tokens = encoding.encode(text)

    truncated_tokens = tokens[:max_tokens]

    truncated_text = encoding.decode(truncated_tokens)

    return truncated_text


def calculate_similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _top_title_similarities(new_title, titles):
    best = []
    for index, title in enumerate(titles):
        matcher = SequenceMatcher(None, new_title, title)
        if len(best) == 3:
            # 상위 3개에 들 수 없는 경우만 정밀 계산을 생략한다.
            floor = best[0][0]
            if matcher.real_quick_ratio() < floor or matcher.quick_ratio() < floor:
                continue
        # 점수가 같으면 기존 목록에서 먼저 나온 제목을 우선한다.
        item = (matcher.ratio(), -index, title)
        if len(best) < 3:
            heapq.heappush(best, item)
        elif item > best[0]:
            heapq.heapreplace(best, item)
    return [(score, title) for score, _, title in sorted(best, reverse=True)]


def recent_title():
    filename = "titles.txt"
    recent_titles = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as file:
            lines = file.readlines()
            for line in lines:
                saved_datetime, saved_title = line.strip().split("$", 1)
                saved_date = _parse_title_timestamp(saved_datetime)
                if saved_date > datetime.now() - timedelta(days=10):
                    recent_titles.append((saved_date, saved_title))
    recent_titles.sort(reverse=True, key=lambda x: x[0])
    recent_titles = [title for _, title in recent_titles[:1000]]
    return recent_titles


def is_recent_title_duplicate(
    new_title,
    filename="titles.txt",
    *,
    identity_title=None,
    announcement=None,
    department="",
):
    duplicate_review.set("")
    duplicate_review_reason.set("")
    recent_titles = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as file:
            lines = file.readlines()
            for line in lines:
                saved_datetime, saved_title = line.strip().split("$", 1)
                saved_date = _parse_title_timestamp(saved_datetime)
                if saved_date > datetime.now() - timedelta(days=10):
                    recent_titles.append((saved_date, saved_title))

    # 최근 10일 이내 제목 중 최대 1,000개를 비교한다.
    recent_titles.sort(reverse=True, key=lambda x: x[0])
    recent_entries = recent_titles[:1000]
    mode = os.getenv("NOTICE_DUPLICATE_MODE") or "hybrid"
    original_title = identity_title or new_title
    if mode != "legacy":
        recent_entries = [
            (date, title)
            for date, title in recent_entries
            if not occurrence_conflict(original_title, title)
        ]
    recent_titles = [title for _, title in recent_entries]

    cleaned_titles = [comparison_title(title, mode) for title in recent_titles]

    if mode != "legacy":
        valid = [
            (entry, cleaned)
            for entry, cleaned in zip(recent_entries, cleaned_titles)
            if cleaned
        ]
        recent_entries = [entry for entry, _ in valid]
        cleaned_titles = [cleaned for _, cleaned in valid]
    query_title = new_title if mode == "legacy" else comparison_title(new_title, mode)
    title_similarities = (
        _top_title_similarities(query_title, cleaned_titles) if mode == "legacy"
        else candidate_title_similarities(new_title, [title for _, title in recent_entries], mode)
    ) if query_title else []
    candidate_labels = [title for _, title in title_similarities[:3]]

    def decision(result, method, similarity=None):
        method = duplicate_review.get() or method
        raw_similarity = max((calculate_similarity(normalize_title(new_title), normalize_title(value)) for value in candidate_labels), default=0.0) if candidate_labels else None
        shown_similarity = similarity if mode == "legacy" else raw_similarity
        event(
            "중복",
            f"중복 체크 결과: {result} ({method}"
            + (f", 제목 유사도 {shown_similarity:.5f}" if shown_similarity is not None else "")
            + ")",
            plain=True,
            result=result,
            method=method,
            similarity=shown_similarity,
            normalized_similarity=similarity,
            candidates=candidate_labels,
            reason=duplicate_review_reason.get(),
        )
        return result

    if len(title_similarities) == 0:
        return decision("중복 아님", "최근 비교 대상 없음")

    highest_similarity = (
        title_similarities[0][0] if mode == "legacy"
        else calculate_similarity(query_title, title_similarities[0][1])
    )

    if mode == "legacy" and highest_similarity >= 0.85:
        return decision("중복", "기존 문자열 기준", highest_similarity)

    if mode == "legacy":
        return decision(
            check_title_similarity(
                new_title, [title for _, title in title_similarities[:3]]
            ),
            "GPT 후보 검토",
            highest_similarity,
        )
    originals = defaultdict(deque)
    for date, title in recent_entries:
        originals[comparison_title(title, mode)].append((date, title))
    candidates = [originals[cleaned].popleft() for _, cleaned in title_similarities[:3]]
    candidate_labels = [title for _, title in candidates]
    current = Notice(original_title, datetime.now().isoformat())
    for date, title in candidates:
        result = compare_notices(current, Notice(title, date.isoformat()))
        if result["relation"] == "same_event" and not requires_department_evidence(original_title):
            return decision("중복", "행사·연도·회차 대조", highest_similarity)
    context = None
    if announcement is not None:
        dates = {title: date for date, title in reversed(candidates)}
        context = lambda titles: duplicate_context(
            announcement, department, titles, dates, BASE_DIR
        )
    options = {"context_provider": context} if context else {}
    return decision(
        check_title_similarity(
            original_title, [title for _, title in candidates], **options
        ),
        "후보 문맥 검토",
        highest_similarity,
    )


def save_title(title, category, filename="sorted_labeled_data.csv"):
    with open(filename, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerow([title, category])


def save_only_title(title):
    with open("titles.txt", "a", encoding="utf-8") as file:
        file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}${title}\n")
