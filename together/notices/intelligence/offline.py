"""API 장애 때 최선의 로컬 판단을 쓰고, 확신과 대체 여부를 명시한다."""
from difflib import SequenceMatcher
import json
import re
from .events import Notice, compare_notices, phase
from .routing import category_hint, configured_classifier
from .policy import KEYWORD_CATEGORIES, CATEGORIES
from .text import occurrence_conflict, requires_department_evidence, normalize_title
from .context import department_conditions_differ


def local_decision(request, base_dir):
    payload = json.loads(request['messages'][-1]['content'])
    task = request['response_format']['json_schema']['name']
    if task == 'notice_category':
        title, body = payload['title'], payload.get('body', '')
        model = configured_classifier(base_dir)
        hint = category_hint(title, KEYWORD_CATEGORIES, model, body=body)
        category = hint['category']
        if not category and model:
            prediction = model.predict(title)
            if prediction.get('matched_features', 0):
                category = prediction['category']
        if not category:
            # 모델 파일까지 없는 비상 상황의 활동 형식 규칙. 광고 필터는 게시 전 별도로 유지한다.
            text = title + ' ' + body
            rules = [
                (r'공모전|경진대회|콘테스트|해커톤', '[공모전] 기타'),
                (r'봉사(?:단|자)|자원봉사|교육봉사', '봉사활동'),
                (r'장학생|장학금.*(?:신청|모집|선발)', '장학금'),
                (r'서포터즈|홍보대사|기자단', '서포터즈'),
                (r'특강|강연|세미나|교육.*(?:운영|신청|참가)|캠프|프로그램', '교육/특강/프로그램'),
                (r'채용|공채|구인|인턴|현장실습', '취업 정보'),
            ]
            category = next((label for pattern, label in rules if re.search(pattern, text)), '해당없음')
        if category not in CATEGORIES:
            category = '해당없음'
        return {'category': category, 'notice_type': 'notice', 'needs_body': False,
                'reason': 'API 오류·로컬 대체 판단, 복구 후 재검토', '_local_fallback': True}
    if task != 'duplicate_decision':
        raise ValueError('Unsupported offline decision task')
    new = payload['new_notice']
    if isinstance(new, str):
        new = {'title': new}
    new.setdefault('title', '')
    candidates = payload.get('candidates') or [{'title': title} for title in payload['candidate_titles']]
    for index, old in enumerate(candidates):
        left, right = new['title'], old['title']
        if occurrence_conflict(left, right) or (phase(left) != phase(right) and 'result' in (phase(left), phase(right))):
            continue
        if compare_notices(Notice(left), Notice(right))['relation'] == 'related_update':
            continue
        context = {'new_notice': new, 'recent_notices': [old]}
        if department_conditions_differ(context):
            continue
        a, b = (re.sub(r'\s+', '', str(row.get('body_excerpt', ''))) for row in (new, old))
        same_source = bool(new.get('source_url') and new.get('source_url') == old.get('source_url'))
        scoped = requires_department_evidence(left) or requires_department_evidence(right)
        if scoped and not (new.get('department') and new.get('department') == old.get('department')):
            continue
        # 제목 유사도만으로 새 공지를 누락하지 않는다. 실제 원문/본문의 일치를 요구한다.
        same_body = len(a) >= 100 and a == b
        if (same_source or same_body) and SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio() >= .72:
            return {'relation': 'same_event', 'matched_index': index, 'reason': '같은 원문 또는 본문·행사 일치', '_local_fallback': True}
    return {'relation': 'needs_context', 'matched_index': -1, 'reason': 'API 오류·로컬 근거 부족, 누락 방지 후 재검토', '_local_fallback': True}
