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
from together.notices.intelligence.context import duplicate_context,excerpt
from together.notices.intelligence.focus import exchange_focus, focus_conflict
from together.operations.events import event, duplicate_review, duplicate_review_reason
from together.operations.events import duplicate_match

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
    current_focus = exchange_focus(new_title)
    rounds = set(identity(new_title)["rounds"])
    scored = []
    for index, (original, value) in enumerate(zip(titles, cleaned)):
        if not value:
            continue
        score = calculate_similarity(query, value)
        if current_ids & identifiers(original) and rounds & set(identity(original)["rounds"]):
            score += 0.35
        previous_focus = exchange_focus(original)
        if (current_focus and previous_focus and current_focus['occurrence_complete']
                and previous_focus['occurrence_complete']
                and current_focus['occurrence_key'] == previous_focus['occurrence_key']):
            # 학교·학기·과정이 같은 후보를 먼저 검토한다. 중복 확정은 하지 않는다.
            score += 0.5
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
    recovery_recheck=False,
    representative_check=None,
):
    duplicate_review.set("")
    duplicate_review_reason.set("")
    duplicate_match.set(None)
    from together.notices.intelligence.policy import scholarship_duplicate_allowed
    if scholarship_duplicate_allowed(identity_title or new_title, department):
        event('중복', '중복 체크 결과: 중복 허용 (장학금, 학과별 신청·추천 조건을 보존하기 위해 다른 학과의 같은 공지도 각각 게시)', plain=True, result='중복 허용', method='scholarship_department_policy', reason='학과별 장학 공지를 중복 판단 없이 게시')
        return '중복 아님'
    mode = os.getenv("NOTICE_DUPLICATE_MODE") or "hybrid"
    # 예전 모드의 10일 범위는 유지하며 현재 방식은 학과 전달 지연을 2주까지 본다.
    lookback_days = 10 if mode == 'legacy' else 14
    recent_titles = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as file:
            lines = file.readlines()
            for line in lines:
                saved_datetime, saved_title = line.strip().split("$", 1)
                saved_date = _parse_title_timestamp(saved_datetime)
                if saved_date > datetime.now() - timedelta(days=lookback_days):
                    recent_titles.append((saved_date, saved_title))

    # 현재 방식은 최근 14일, 예전 방식은 10일 제목 중 최대 1,000개를 비교한다.
    recent_titles.sort(reverse=True, key=lambda x: x[0])
    recent_entries = recent_titles[:1000]
    original_title = identity_title or new_title
    if mode != 'legacy' and len(normalize_title(original_title)) >= 20 and identity(original_title)['years']:
        # 학과 전달 제목이 많이 쌓여도 14일 안의 같은 유명 행사 제목을
        # 1,000행 경계 때문에 후보에서 잃지 않는다. 실제 대표 확인은 유지한다.
        exact=normalize_title(original_title)
        recent_entries += [entry for entry in recent_titles[1000:]
                           if normalize_title(entry[1])==exact][:3]
    if recovery_recheck:
        recent_entries = [(date, title) for date, title in recent_entries if normalize_title(title) != normalize_title(original_title)]
    if mode != "legacy":
        recent_entries = [
            (date, title)
            for date, title in recent_entries
            if not occurrence_conflict(original_title, title)
            and not focus_conflict(original_title, title)
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
        nonlocal candidate_labels
        method = duplicate_review.get() or method
        if result=='중복' and representative_check is not None:
            from together.notices.intelligence.representative import RepresentativeUnconfirmed
            evidence=duplicate_match.get() or {'title':candidate_labels[0] if candidate_labels else ''}
            proof=representative_check(evidence)
            if proof.get('status')=='recheck':
                actual=proof['_actual_candidates']
                current={'title':original_title,'source_url':getattr(announcement,'url',''),
                         'department':department,'body_excerpt':excerpt(getattr(announcement,'content_text',''))}
                def actual_context(titles):
                    previous=[next(v for v in actual if v['title']==title) for title in titles]
                    return {'new_notice':current,'recent_notices':[dict(v,body_excerpt=excerpt(v.get('body_excerpt',''))) for v in previous]}
                candidate_labels=[v['title'] for v in actual]
                result=check_title_similarity(original_title,candidate_labels,context_provider=actual_context)
                method=duplicate_review.get() or '실제 게시 원문 후보 재검토'
                if result=='중복':
                    proof=representative_check(duplicate_match.get() or {})
                else:
                    proof={'status':'verified'}  # 중복 확정이 철회돼 새 글 보존 경로로 진행한다.
            if proof.get('status')=='first_publication':
                result,method='중복 아님','게시·저장 이력 없음·첫 공지 우선 게시'
                duplicate_review_reason.set('비슷한 제목은 있으나 실제 게시·저장 이력이 없어 첫 공지를 먼저 게시')
            elif proof.get('status')=='missing':
                result,method='중복 아님','대표 게시글 없음·새 공지 보존'
                duplicate_review_reason.set('기존 후보의 실제 게시글이 없어 이번 공지를 새 글로 게시')
            elif proof.get('status')!='verified':
                raise RepresentativeUnconfirmed('Actual representative needs reconciliation before duplicate suppression')
        raw_similarity = max((calculate_similarity(normalize_title(new_title), normalize_title(value)) for value in candidate_labels), default=0.0) if candidate_labels else None
        shown_similarity = similarity if mode == "legacy" else raw_similarity
        from together.operations.events import decision_explanation
        explanation = decision_explanation(method, duplicate_review_reason.get())
        event(
            "중복",
            f"중복 체크 결과: {result} ({explanation}"
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
            duplicate_match.set({'title':title,'observed_at':date.isoformat()})
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
