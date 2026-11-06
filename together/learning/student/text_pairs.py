import re
from difflib import SequenceMatcher
from functools import lru_cache
from together.notices.intelligence.text import normalize_title
from together.notices.intelligence.focus import exchange_focus
def compact(text):
    return ''.join(c for c in normalize_title(text).casefold() if c.isalnum())

def grams(text, n):
    return {text[i:i+n] for i in range(len(text)-n+1)}

def overlap(a,b):
    common=len(a & b)
    return [common/max(1,len(a|b)), common/max(1,min(len(a),len(b))), float(bool(a and b) and a==b)]

def text_pair(a,b,full=False):
    x,y=compact(a),compact(b)
    v=[min(len(x),len(y))/max(len(x),len(y),1), float(x==y and bool(x))]
    if not full: v.append(SequenceMatcher(None,x,y,autojunk=False).ratio())
    for size in (2,4): v.extend(overlap(grams(x,size),grams(y,size)))
    v+=overlap(set(re.findall(r'[가-힣A-Za-z0-9]+',a)),set(re.findall(r'[가-힣A-Za-z0-9]+',b)))
    for pat in (r'(?:19|20)\d\d',r'\d+\s*(?:학기|회|차|기|월|일|시)',r'[A-Za-z]{2,}',r'[가-힣]{2,}대(?:학교)?',r'\d+'):
        v+=overlap(set(re.findall(pat,a)),set(re.findall(pat,b)))
    return v

def title_vector(a,b):
    x,y=a['title'],b['title'];v=text_pair(x,y)
    strip=lambda t:re.sub(r'^\s*(?:\[[^\]]{1,32}\]\s*)+','',t)
    v+=text_pair(strip(x),strip(y))[:6]
    for pat in ('추가|연장|재안내|재공고','정정|변경','합격|선발결과|발표','학부|대학원','교류|교환|수학','참여기업|실습기관','학생 모집|학생모집'):
        flags=[bool(re.search(pat,s)) for s in (x,y)];v += [float(all(flags)),float(flags[0]!=flags[1])]
    return v

def body_vector(a,b):
    x=(a.get('body') or '')[:4000];y=(b.get('body') or '')[:4000]
    v=text_pair(x,y,True)+[min(len(x),len(y)),max(len(x),len(y))]
    for pat in (r'https?://[^\s<>()\]]+',r'[\w.+-]+@[\w.-]+',r'\b0\d{1,2}[- ]\d{3,4}[- ]\d{4}',r'\d{1,2}\s*[./월]\s*\d{1,2}',r'[^.!?\n]{0,18}(?:마감|까지)'):
        v+=overlap(set(re.findall(pat,x)),set(re.findall(pat,y)))
    for note,other in [(a,y),(b,x)]:
        v+=overlap(grams(compact(note['title']),3),grams(compact(other),3))[:2]
    focus=[exchange_focus(n['title'],n.get('body','')) or {} for n in (a,b)]
    for key in ['target_schools','years','terms','programs']:
        v+=overlap(set(focus[0].get(key,[])),set(focus[1].get(key,[])))
    return v
