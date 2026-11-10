"""반복 공지에서 중요한 항목과 실제 게시일을 분리한다.

행사군을 기억하는 것과 같은 연도·학기 발생 건을 합치는 것은 별개다.
추출 실패는 미확인이며, 비슷한 날짜만으로 중복을 확정하지 않는다.
"""
from datetime import datetime
import re
from .text import identity, normalize_title


_UNIVERSITIES = (
    '서울시립대학교', '부산외국어대학교', '한국외국어대학교', '서울과학기술대학교',
    '한국교원대학교', '한국교통대학교', '한국해양대학교', '국립한국해양대학교',
    '한국항공대학교', '한국기술교육대학교', '경상국립대학교', '경상대학교',
    '부경대학교', '국립부경대학교', '공주대학교', '국립공주대학교',
    '충남대학교', '충북대학교', '강원대학교', '경북대학교', '전남대학교',
    '전북대학교', '제주대학교', '군산대학교', '국립군산대학교',
    '순천대학교', '국립순천대학교', '창원대학교', '국립창원대학교',
    '금오공과대학교', '국립금오공과대학교', '한밭대학교', '국립한밭대학교',
    '목포대학교', '국립목포대학교', '서울대학교', '부산대학교', '서강대학교',
    '연세대학교', '고려대학교', '한양대학교', '성균관대학교', '경희대학교',
    '중앙대학교', '동국대학교', '건국대학교', '홍익대학교', '숭실대학교',
    '숙명여자대학교', '이화여자대학교', '동아대학교', '동의대학교',
    '울산대학교', '인제대학교', '경성대학교', '신라대학교', '영남대학교',
    '계명대학교', '대구대학교', '한동대학교', '포항공과대학교',
)
_ALIASES = {name:name for name in _UNIVERSITIES}
for _name in _UNIVERSITIES:
    _ALIASES.setdefault(_name.removesuffix('학교'), _name)
    _ALIASES.setdefault(_name.removesuffix('대학교')+'대', _name)
_ALIASES.update({'부산외대':'부산외국어대학교','한국외대':'한국외국어대학교',
                 '경상대학교':'경상국립대학교','경상대':'경상국립대학교',
                 '국립한국해양대학교':'한국해양대학교', '국립부경대학교':'부경대학교'})
for _name in _UNIVERSITIES:
    if _name.startswith('국립'):
        _ALIASES[_name] = _ALIASES.get(_name[2:], _name[2:])
for _alias, _name in list(_ALIASES.items()):
    _ALIASES[_alias] = '경상국립대학교' if _name in ('경상대학교','경상국립대학교') else _name.removeprefix('국립')
_SCHOOL_PATTERN = re.compile('|'.join(re.escape(v) for v in sorted(_ALIASES, key=len, reverse=True)))
_EXCHANGE = re.compile(r'교류\s*수학|학점\s*교류|교환\s*수학')
_PROGRAMS = ('국제동계대학','국제하계대학','에너지신산업','혁신융합대학','에리카','ERICA')


def exchange_focus(title, body=''):
    title, body = normalize_title(title), normalize_title(body)
    if not _EXCHANGE.search(title):
        return None
    schools = sorted({_ALIASES[m.group()] for m in _SCHOOL_PATTERN.finditer(title)})
    school_source = 'title'
    if not schools:
        # 본문에 주관 대학·문의처·예시 학교가 여러 개 있으면 확정하지 않는다.
        schools = sorted({_ALIASES[m.group()] for m in _SCHOOL_PATTERN.finditer(body)} - {'부산대학교'})
        school_source = 'body_unique_unconfirmed' if len(schools)==1 else 'unconfirmed'
    details = identity(title)
    if not details['years']:
        years = sorted(set(re.findall(r'(?<!\d)((?:19|20)\d{2})\s*학년도', body)))
        if len(years)==1:
            details['years'] = years
    seasons = set(details['seasons'])
    for text, season in ((r'겨울|동계','winter'),(r'여름|하계','summer')):
        if re.search(text,title): seasons.add(season)
    terms = sorted(seasons | {'semester_'+s for s in details['semesters']})
    programs = sorted({('ERICA' if v in ('에리카','ERICA') else v) for v in _PROGRAMS if v.casefold() in title.casefold()})
    years = details['years']
    return {'kind':'exchange_study','target_schools':schools,'school_source':school_source,
            'years':years,'terms':terms,'programs':programs,
            'occurrence_complete':school_source=='title' and len(schools)==1 and len(years)==1 and len(terms)==1,
            'family_memory_key':['exchange_study',schools,programs],
            'occurrence_key':['exchange_study',schools,years,terms,programs]}


def _pnu_hiring_employer(title):
    """채용 공고에 명시된 부산대 소속 기관만 추출한다. 전달 학과는 쓰지 않는다."""
    value=normalize_title(title)
    if not re.search(r'채용|서류전형.*합격자',value) or re.search(r'설명회|박람회|특강|교육생',value):
        return None
    value=re.sub(r'부산대(?!학교)', '부산대학교',value)
    prefix=re.split(r'서류전형|공개\s*채용|공개경쟁|채용',value,maxsplit=1)[0]
    match=re.search(r'부산대학교\s*([가-힣A-Za-z·\s]{2,45}?(?:재단|개발원|협력단|연구원|교육원|센터|사업단|학과|학부|대학원|도서관|박물관|본부))',prefix)
    if not match:return None
    employer=re.sub(r'\s+','',match[1])
    return None if employer=='연구원' else employer


def focus_conflict(left_title, right_title, left_body='', right_body=''):
    employers=[_pnu_hiring_employer(t) for t in (left_title,right_title)]
    if all(employers) and employers[0]!=employers[1]:
        return 'hiring_employer_conflict'
    first, second = exchange_focus(left_title,left_body), exchange_focus(right_title,right_body)
    if not first or not second:
        return None
    for key in ('target_schools','years','terms','programs'):
        if key=='target_schools' and (first['school_source']!='title' or second['school_source']!='title'):
            continue
        a,b = first[key],second[key]
        if a and b and not set(a)&set(b):
            return 'exchange_'+key+'_conflict'
    return None


def dispatch_evidence(left, right):
    """공식 게시일이 둘 다 있을 때만 행정 전파 시간의 보조 특징을 만든다."""
    result = {'explicit_publication_dates':False,'days_apart':None,
              'within_two_days':False,'within_seven_days':False,'within_fourteen_days':False,
              'same_collection_time_is_not_event_evidence':True,
              'alone_can_confirm_duplicate':False}
    if not all(v.get('publication_time_is_explicit') for v in (left,right)):
        return result
    try:
        dates = [datetime.fromisoformat(v['publication_time']).date() for v in (left,right)]
    except (KeyError,ValueError,TypeError):
        return result
    gap = abs((dates[0]-dates[1]).days)
    result.update(explicit_publication_dates=True,days_apart=gap,
                  within_two_days=gap<=2,within_seven_days=gap<=7,within_fourteen_days=gap<=14)
    return result


def focus_text(title, body=''):
    """중요 항목을 앞에 놓고 원래 숫자·대학명·본문을 함께 전달한다."""
    focus = exchange_focus(title,body)
    if not focus:
        return title+'\n'+body
    terms = {'winter':'겨울계절','summer':'여름계절','semester_1':'1학기','semester_2':'2학기'}
    prefix = '\n'.join(('활동: 교류수학',
        '대상 대학: '+', '.join(focus['target_schools']),
        '학년도: '+', '.join(focus['years']),
        '수학 학기: '+', '.join(terms[v] for v in focus['terms']),
        '세부 과정: '+', '.join(focus['programs'])))
    return prefix+'\n원래 제목: '+title+'\n원문: '+body
