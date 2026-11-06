import re
from difflib import SequenceMatcher
from together.notices.intelligence.text import family_key, identity, occurrence_conflict, requires_department_evidence
def overlap(a,b):return len(a&b)/len(a|b) if a|b else 0

def scalar_features(left,right):
 a,b=left['title'],right['title'];x,y=family_key(a),family_key(b);bodya,bodyb=left.get('body',''),right.get('body','');compact=lambda t:re.sub(r'\s+','',t)
 ca,cb=compact(bodya),compact(bodyb);ia,ib=identity(a),identity(b);conflict=occurrence_conflict(a,b)
 features=[SequenceMatcher(None,x,y,autojunk=False).ratio(),min(len(x),len(y))/max(1,len(x),len(y)),int(x==y),int(conflict=='years'),int(bool(conflict and conflict.startswith('round_'))),int(conflict=='semesters'),int(conflict=='seasons'),int(requires_department_evidence(a) or requires_department_evidence(b)),overlap(set(re.findall(r'\d+',a)),set(re.findall(r'\d+',b))),int(len(ca)>=80),int(len(cb)>=80),int(len(ca)>=80 and ca==cb),int(bool(left.get('department')) and left.get('department')==right.get('department')),int(bool(left.get('source_url')) and left.get('source_url')==right.get('source_url')),overlap(set(re.findall(r'\d+',bodya)),set(re.findall(r'\d+',bodyb)))]
 for name in ('years','rounds','semesters','seasons'):
  features.extend([int(bool(ia[name])!=bool(ib[name])),overlap(set(ia[name]),set(ib[name]))])
 for pattern in (r'추가|연장|변경|수정',r'결과|합격|수상',r'신청|모집|접수',r'장학|조교|수강|졸업',r'채용|공채|인턴',r'특강|세미나|교육|캠프'):
  features.extend([int(bool(re.search(pattern,a))!=bool(re.search(pattern,b))),int(bool(re.search(pattern,a+b)))])
 return features
