import json
import os
from together.operations.events import duplicate_review, duplicate_review_reason

from openai import OpenAI

from together.config import BASE_DIR
from together.notices.intelligence.prompts import category_request, duplicate_request
from together.notices.intelligence.text import (
    occurrence_conflict,
    requires_department_evidence,
)
from together.storage.api_usage import ApiAccounting
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
    # 학과별 모집은 모델이 동일 제목을 중복으로 판단하더라도 근거 없이 누락하지 않는다.
    if requires_department_evidence(new_title):
        duplicate_review.set("학과별 모집·대상 확인 필요")
        return "중복 아님"
    candidates = [
        title
        for title in recent_titles[:3]
        if not occurrence_conflict(new_title, title)
        and not requires_department_evidence(title)
    ]
    if not candidates:
        duplicate_review.set("연도·회차·학과 조건에 맞는 후보 없음")
        return "중복 아님"
    request = duplicate_request(new_title, candidates)
    result = _decision(DUPLICATE_MODEL, request)
    used_context = False
    if result.get("relation") == "needs_context" and context_provider is not None:
        context = context_provider(candidates)
        if context:
            ApiAccounting(BASE_DIR).store(DUPLICATE_MODEL, request, result)
            request = duplicate_request(new_title, candidates, context)
            result = _decision(DUPLICATE_MODEL, request)
            used_context = True
    relation = result.get("relation")
    duplicate_review_reason.set(str(result.get("reason", "")))
    labels = {
        "same_event": "동일 행사",
        "different_event": "별도 행사",
        "related_update": "변경·추가 안내",
        "needs_context": "동일성 확인 보류",
    }
    duplicate_review.set(
        ("GPT 본문 검토·" if used_context else "GPT 후보 검토·")
        + labels.get(relation, "판정값 확인 필요")
    )
    if relation == "same_event":
        index = result.get("matched_index")
        if type(index) is not int or not 0 <= index < len(candidates):
            raise ValueError("Invalid duplicate match index")
        ApiAccounting(BASE_DIR).store(DUPLICATE_MODEL, request, result)
        return "중복"
    if relation in ("different_event", "related_update", "needs_context"):
        ApiAccounting(BASE_DIR).store(DUPLICATE_MODEL, request, result)
        return "중복 아님"
    raise ValueError("Unknown duplicate relation")
