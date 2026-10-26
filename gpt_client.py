import json
import os

from openai import OpenAI

from runtime_config import BASE_DIR
from notice_intelligence.prompts import category_request, duplicate_request
from notice_intelligence.text import occurrence_conflict, requires_department_evidence

with (BASE_DIR / 'env.json').open(encoding='utf-8') as handle:
    CATEGORIES = json.load(handle)['CATEGORIES']

client = OpenAI(api_key=os.getenv('GPT_API_KEY'), timeout=30, max_retries=1)
CATEGORY_MODEL = os.getenv('GPT_CATEGORY_MODEL') or 'gpt-5.4-mini-2026-03-17'
DUPLICATE_MODEL = os.getenv('GPT_DUPLICATE_MODEL') or 'gpt-5.4-mini-2026-03-17'


def _decision(model, request):
    response = client.chat.completions.create(model=model, max_completion_tokens=500,
                                             reasoning_effort='none', **request)
    content = response.choices[0].message.content
    if not content:
        raise ValueError('Empty classification response')
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError('Invalid classification response')
    return result


def answer_gpt(user_content):
    title, separator, body = user_content.partition('\n')
    result = _decision(CATEGORY_MODEL, category_request(title, CATEGORIES, body if separator else ''))
    category = result.get('category')
    if category == '판단보류':
        print('분류 보류: 기타 공지사항으로 처리합니다.', flush=True)
        return '해당없음'
    if category not in CATEGORIES:
        raise ValueError('Unknown notice category')
    return category


def check_title_similarity(new_title, recent_titles):
    # 학과별 모집은 모델이 동일 제목을 중복으로 판단하더라도 근거 없이 누락하지 않는다.
    if requires_department_evidence(new_title):
        return '중복 아님'
    candidates = [title for title in recent_titles[:3]
                  if not occurrence_conflict(new_title, title) and not requires_department_evidence(title)]
    if not candidates:
        return '중복 아님'
    result = _decision(DUPLICATE_MODEL, duplicate_request(new_title, candidates))
    relation = result.get('relation')
    if relation == 'same_event':
        index = result.get('matched_index')
        if type(index) is not int or not 0 <= index < len(candidates):
            raise ValueError('Invalid duplicate match index')
        return '중복'
    if relation in ('different_event', 'related_update', 'needs_context'):
        return '중복 아님'
    raise ValueError('Unknown duplicate relation')
