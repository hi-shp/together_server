import re
from .scalars import scalar_features
from together.learning.inputs import keyword_signals
from together.notices.intelligence.text import normalize_title
from together.notices.intelligence.policy import CATEGORIES
from together.notices.intelligence.focus import exchange_focus,dispatch_evidence
def dice(a,b):return 2*len(a&b)/max(1,len(a)+len(b))

def grams(text,n):
    text=re.sub(r'\s+','',normalize_title(text));return {text[i:i+n] for i in range(max(0,len(text)-n+1))}

def pair_features(row):
    a,b=row['left'],row['right'];features=scalar_features(a,b)
    for field in ('title','body'):
        x,y=a.get(field,''),b.get(field,'')
        for n in (1,2,3,4):features.append(dice(grams(x,n),grams(y,n)))
        features.extend([dice(set(re.findall(r'[가-힣A-Za-z]+',x)),set(re.findall(r'[가-힣A-Za-z]+',y))),
                         dice(set(re.findall(r'\d+',x)),set(re.findall(r'\d+',y)))])
    for label in CATEGORIES:
        flags=[]
        for side in (a,b):
            signals=keyword_signals(side['title'],side.get('body',''))
            flags.append(any(v['category']==label for v in signals['title_hits']+signals['body_only_hits']))
        features.extend([float(flags[0] and flags[1]),float(flags[0]!=flags[1])])
    focus=[exchange_focus(v['title'],v.get('body','')) for v in (a,b)]
    features.extend([float(bool(v)) for v in focus])
    for key in ('target_schools','years','terms','programs'):
        left,right=[set(v[key]) if v else set() for v in focus]
        features.extend([float(bool(left) and bool(right)),float(bool(left) and left==right),float(bool(left) and bool(right) and left!=right)])
    time=dispatch_evidence(a,b)
    features.extend(float(time[k]) for k in ('explicit_publication_dates','within_two_days','within_seven_days','within_fourteen_days'))
    for pattern in (r'마감|까지|접수기간|신청기간',r'모집대상|지원대상|지원자격|참여대상',r'학부생|학부재학생',r'대학원생|석사|박사',r'교직원|교원',r'정정|연장|추가모집|변경',r'선발결과|선정결과|합격자|수상자',r'서울시립|서울대|서강|한양'):
        flags=[bool(re.search(pattern,re.sub(r'\s+','',v['title']+' '+v.get('body','')))) for v in (a,b)]
        features.extend([float(all(flags)),float(flags[0]!=flags[1])])
    return features
