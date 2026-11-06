import re,unicodedata
from .evidence import hard_relation
def normalize(t):
 t=unicodedata.normalize('NFKC',t or '')
 t=re.sub(r'https?://\S+|\b[\w.+-]+@[\w.-]+\.\w+',' ',t)
 return re.sub(r'\s+',' ',t).strip()

def body(n):return normalize(n.get('body',''))

def intent(n):
 # Preserve purpose/deliverables and who applies, rather than dates/forwarding boilerplate.
 t=n['title'];lines=re.split(r'[\n;]|(?<=[다요]\.)\s+',n.get('body',''))
 cues=r'공모.{0,5}(?:주제|분야|내용)|제출.{0,5}(?:작품|내용|자료)|심사.{0,5}(?:기준|방법)|지원.{0,5}(?:내용|자격)|활동.{0,5}내용|모집.{0,5}대상|참가.{0,5}대상|주요.{0,5}내용|교육.{0,5}과정|수강|학점|수료|봉사|업무|인건비|수상|선발.?결과|공동구매|교육과정.{0,12}개정'
 selected=[normalize(s)[:350] for s in lines if re.search(cues,s)]
 return t+' '+ ' '.join(selected[:16])+' '+body(n)[:700]

def tokens(t):return set(re.findall(r'[가-힣A-Za-z]{2,}|\d+(?:[./]\d+)+',normalize(t)))

def pairtext(r):
 a,b=r['left'],r['right'];ta,tb=tokens(a['title']),tokens(b['title']);ba,bb=tokens(intent(a)),tokens(intent(b))
 return ' '.join(['TITLE_SHARED_'+w for w in sorted(ta&tb)]+['TITLE_DIFF_'+w for w in sorted(ta^tb)]+['BODY_SHARED_'+w for w in sorted(ba&bb)]+['BODY_DIFF_'+w for w in sorted(ba^bb)])

def anchors(n):
 # Main subject / first paragraph only: unrelated historical reference dates are not event identity.
 text=n['title']+' '+body(n)[:320]
 years=set(re.findall(r'(20\d\d)\s*학년도',text))
 terms=set(re.findall(r'([12])\s*학기',text))
 rounds=set(re.findall(r'제\s*(\d+)\s*(?:회|기|차)(?!간)',text))
 return years,terms,rounds

def conflict(r):
 reason,why=hard_relation(r)
 if reason:return reason,why
 a,b=r['left'],r['right']
 for x,y,tag in zip(anchors(a),anchors(b),['본문 학년도','본문 학기','본문 회차']):
  if x and y and x.isdisjoint(y):return 'different_event',tag
 return '',''

def focus_features(r):
 a,b=r['left'],r['right'];aa,bb=anchors(a),anchors(b);out=[]
 for x,y in zip(aa,bb):out += [bool(x),bool(y),bool(x and y and x.isdisjoint(y)),len(x&y)]
 for selector in [lambda n:n['title'],body,intent]:
  x,y=[tokens(selector(n)) for n in (a,b)]
  out += [len(x),len(y),len(x&y)/max(1,len(x|y)),len(x&y)/max(1,min(len(x),len(y)))]
 rel,_=conflict(r);out += [rel==x for x in ['different_event','related_update','needs_context']]
 return out
