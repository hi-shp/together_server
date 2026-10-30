import json
import os
import re
from together.operations.events import duplicate_review, duplicate_review_reason

from openai import OpenAI

from together.config import BASE_DIR
from together.notices.intelligence.prompts import category_request, duplicate_request
from together.notices.intelligence.text import (
    occurrence_conflict,
    requires_department_evidence,
    normalize_title,
)
from together.storage.api_usage import ApiAccounting
from together.notices.intelligence.context import department_conditions_differ
from together.notices.intelligence.quality import remember_review

from together.notices.intelligence.policy import CATEGORIES


client = OpenAI(api_key=os.getenv("GPT_API_KEY"), timeout=30, max_retries=1)
CATEGORY_MODEL = os.getenv("GPT_CATEGORY_MODEL") or "gpt-5.4-mini-2026-03-17"
DUPLICATE_MODEL = os.getenv("GPT_DUPLICATE_MODEL") or "gpt-5.4-mini-2026-03-17"


def _decision(model, request):
    accounting = ApiAccounting(BASE_DIR)
    cached = accounting.cached(model, request)
    if cached is not None:
        return cached
    try:
        accounting.check_budget()
        response = client.chat.completions.create(
            model=model, max_completion_tokens=500, reasoning_effort="none", **request
        )
    except Exception as exc:
        accounting.record(model, request, error=exc)
        raise
    accounting.record(model, request, response=response)
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Empty classification response")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Invalid classification response")
    return result


def answer_gpt(user_content):
    title, separator, body = user_content.partition("\n")
    request = category_request(title, CATEGORIES, body if separator else "")
    result = _decision(CATEGORY_MODEL, request)
    category = result.get("category")
    remember_review(user_content, result)
    if category == "판단보류":
        ApiAccounting(BASE_DIR).store(CATEGORY_MODEL, request, result)
        print("분류 보류: 기타 공지사항으로 처리합니다.", flush=True)
        return "해당없음"
    if category not in CATEGORIES:
        raise ValueError("Unknown notice category")
    ApiAccounting(BASE_DIR).store(CATEGORY_MODEL, request, result)
    return category


def check_title_similarity(new_title, recent_titles, *, context_provider=None):
    shared_program = lambda title: "현장실습학기제" in normalize_title(title).replace(" ", "")
    shared_exchange = lambda title: bool(
        re.search(
            r"계절(?:학기|수업).*교류\s*수학",
            normalize_title(title),
        )
    )
    shared_notice = lambda title: shared_program(title) or shared_exchange(title)
    # 학과별 모집은 모델이 동일 제목을 중복으로 판단하더라도 근거 없이 누락하지 않는다.
    if requires_department_evidence(new_title):
        context = context_provider(recent_titles[:3]) if context_provider else None
        if context:
            source = context.get("new_notice", {}).get("source_url")
            if source and any(
                row.get("source_url") == source
                and normalize_title(row.get("title", "")) == normalize_title(new_title)
                for row in context.get("recent_notices", [])
            ):
                duplicate_review.set("같은 원문·제목 재확인")
                return "중복"
        if not shared_notice(new_title):
            duplicate_review.set("학과별 모집·대상 확인 필요")
            return "중복 아님"
    candidates = [
        title
        for title in recent_titles[:3]
        if not occurrence_conflict(new_title, title)
        and (
            not requires_department_evidence(title)
            or (shared_notice(new_title) and shared_notice(title))
        )
    ]
    if not candidates:
        duplicate_review.set("연도·회차·학과 조건에 맞는 후보 없음")
        return "중복 아님"
    used_context = False
    used_model = False
    reviews = []
    final_relation = "different_event"
    labels = {
        "same_event": "동일 행사",
        "different_event": "별도 행사",
        "related_update": "변경·추가 안내",
        "needs_context": "동일성 확인 보류",
    }
    for position, candidate in enumerate(candidates):
        context = context_provider([candidate]) if context_provider else None
        if shared_notice(new_title) and (
            not context
            or len(context.get("new_notice", {}).get("body_excerpt", "").strip()) < 80
            or not any(len(row.get("body_excerpt", "").strip()) >= 80 for row in context.get("recent_notices", []))
        ):
            final_relation = "needs_context"
            reviews.append(f"{position}번 후보: 학과별 조건을 대조할 본문 부족")
            continue
        if shared_notice(new_title) and department_conditions_differ(context):
            final_relation = "related_update"
            reviews.append(f"{position}번 후보: 학과별 제출·학점 조건 추가")
            continue
        used_context = used_context or bool(context)
        request = duplicate_request(new_title, [candidate], context)
        used_model = True
        result = _decision(DUPLICATE_MODEL, request)
        relation = result.get("relation")
        if relation not in labels:
            raise ValueError("Unknown duplicate relation")
        if relation == "same_event" and (
            type(result.get("matched_index")) is not int
            or result["matched_index"] != 0
        ):
            raise ValueError("Invalid duplicate match index")
        ApiAccounting(BASE_DIR).store(DUPLICATE_MODEL, request, result)
        reviews.append(f"{position}번 후보: {result.get('reason', '')}")
        if relation == "same_event":
            final_relation = relation
            break
        if relation in ("related_update", "needs_context"):
            final_relation = relation
    duplicate_review_reason.set(" / ".join(reviews))
    duplicate_review.set(
        ("본문 조건 대조·" if not used_model else "GPT 본문 검토·" if used_context else "GPT 후보 검토·")
        + labels[final_relation]
    )
    return "중복" if final_relation == "same_event" else "중복 아님"
