"""통합 학생 모델 입력: 기존 규칙은 참고 특징이며 정답으로 강제하지 않는다."""
from together.notices.intelligence.policy import KEYWORD_CATEGORIES,EXCLUDE_KEYWORDS
from together.notices.intelligence.focus import focus_text,dispatch_evidence

INPUT_VERSION='notice_focus_keywords_v1'


def keyword_signals(title,body='',keyword_categories=None,exclude_keywords=None):
    keywords=KEYWORD_CATEGORIES if keyword_categories is None else keyword_categories
    excluded=EXCLUDE_KEYWORDS if exclude_keywords is None else exclude_keywords
    title_hits=[{'keyword':word,'category':category} for word,category in keywords.items() if word in title]
    body_hits=[{'keyword':word,'category':category} for word,category in keywords.items() if word in body and word not in title]
    exclude_hits=[word for word in excluded if word in title+body]
    proposal=title_hits[0]['category'] if title_hits else '해당없음' if exclude_hits else None
    return {'title_hits':title_hits,'body_only_hits':body_hits,'administrative_hits':exclude_hits,
            'legacy_first_match_proposal':proposal,'forces_category':False}


def category_input(title,body='',*,keyword_categories=None,exclude_keywords=None):
    signals=keyword_signals(title,body,keyword_categories,exclude_keywords)
    # 제안 카테고리를 정답 토큰으로 제시하지 않고 키워드가 있다는 사실만 준다.
    hints=' · '.join(v['keyword']+'→'+v['category'] for v in signals['title_hits'])
    administrative=' · '.join(signals['administrative_hits'])
    return ('제목 키워드 참고(정답 아님): '+hints+'\n'
            +'행정·후속처리 단어 참고: '+administrative+'\n'
            +focus_text(title,body))


def pair_input(left,right):
    def render(row):
        return focus_text(row['title'],row.get('body',row.get('body_excerpt','')))+'\n담당 조직: '+row.get('department','')
    # 모델에서 비교하는 두 입력을 분리하되, 수집 시각은 게시일로 넣지 않는다.
    first,second=render(left),render(right)
    time=dispatch_evidence(left,right)
    if time['explicit_publication_dates']:
        first+='\n행정 전달 시간 보조 근거(이것만으로 동일 행사 아님): '+str(time['days_apart'])+'일 차이'
    return first,second
