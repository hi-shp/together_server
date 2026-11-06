import re
from functools import lru_cache
from .archive import pair_input
from .focus import pair_features
PATTERNS=[
r'[「『“]([^」』”]{3,90})[」』”]',r'(?:20\d\d[.년/ -]*)?\d{1,2}[./월-]\s*\d{1,2}(?:일)?',r'제?\s*\d+\s*(?:학기|차|기|회|주년)',
r'[가-힣]{2,12}(?:대학교|대학원|학과|공사|재단|센터|연구소|연구원)',r'(?:학부생|대학원생|외국인|신입생|졸업예정자|석사|박사|재학생|교원|참여기업|실습기관)',
r'(?:마감|추가모집|기간연장|일정변경|정정|합격자|선발결과|결과발표|설명회|접수|등록|공모전|연수|특강|장학생)',
r'[A-Za-z][A-Za-z0-9-]{2,}',r'20\d{2}',r'\d{1,2}시\s*(?:\d{1,2}분)?']

def simple(s):return re.sub(r'\s+','',s).lower()

def normalize_body(s):return re.sub(r'\s+',' ',s or '').strip()

def spans(title,body):
    text=simple(title+' '+body[:3500]);return [set(re.findall(p,text,re.I)) for p in PATTERNS]

def extended(r):
    a,b=r['left'],r['right'];ae,be=(spans(n['title'],n.get('body') or '') for n in (a,b))
    v=pair_input(r,pair_features)
    for l,h in zip(ae,be):v.extend([bool(l),bool(h),len(l&h)/max(1,len(l|h)),bool(l and h and l.isdisjoint(h)),len(l-h),len(h-l)])
    for k in ('title','body'):
        x,y=(simple(n.get(k,'') or '') for n in (a,b))
        v.extend([x==y and bool(x),bool(x and y and (x in y or y in x))])
    return v
