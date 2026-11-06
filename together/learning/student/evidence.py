import re,collections,unicodedata
from urllib.parse import urlsplit
DATE=re.compile(r'(?:(20\d{2})\s*(?:년|[./-])\s*)?(?<!\d)(1[0-2]|0?[1-9])\s*(?:월|[./-])\s*(3[01]|[12]\d|0?[1-9])(?!\d)')

CUE=re.compile(r'신청|접수|지원|제출|추천|마감')

def date_role(before,after):
 context=before[-50:]+' '+after[:100]
 if re.search(r'까지\s*(?:교육에\s*)?(?:정상\s*)?(?:참여|출석|이수|수료)',after):return 'completion'
 if re.search(r'신청한.{0,40}(?:학생|대상)',after) and re.search(r'상품권|혜택|선착순|무상\s*제공|컨설팅',after):return 'benefit'
 if re.search(r'단과대학\s*추천|단과대학\s*[→↔]|국제처.{0,12}(?:제출|전달)',context):return 'administrative'
 if re.search(r'추천\s*기한',before[-35:]):return 'recommend'
 return 'local' if re.search(r'학과|학부|전공|단과|행정실',context) else 'apply'

def deadlines(n):
 out=collections.defaultdict(set)
 text=unicodedata.normalize('NFKC',n['title']+'\n'+n.get('body',''))
 for line in text.splitlines():
  if len(line)>400:
   chunks=re.split(r'(?<=다\.)\s+',line)
  else:chunks=[line]
  for z in chunks:
   if not CUE.search(z) or not ('까지' in z or re.search(r'(?:마감|기한|기간)\s*[:：]',z)):continue
   if re.search(r'결과\s*발표|합격자\s*발표|사업기간|운영기간',z) and not re.search(r'(?:신청|접수|제출|추천)\s*(?:기한|기간|마감|방법)',z):continue
   end=z.find('까지');span=z[:end] if end>=0 else z
   found=list(DATE.finditer(span))
   if not found:continue
   y,m,d=found[-1].groups()
   tail=span[found[-1].end():]
   h=re.search(r'(오전|오후)?\s*(\d{1,2})(?::(\d\d)|시)',tail)
   hour=minute=-1
   if h:
    mer,hh,mm=h.groups();hour=int(hh);minute=int(mm or 0)
    if mer=='오후' and hour<12:hour+=12
   role=date_role(span[:found[-1].start()],z[found[-1].end():])
   out[role].add((int(y or 0),int(m),int(d),hour,minute))
 # PDF-to-HTML often places every word on its own line. Recover closing dates
 # with a short, explicit request context instead of treating those as missing.
 flat=' '.join(text.split())
 for m in DATE.finditer(flat):
  tail=flat[m.end():m.end()+38]
  end=tail.find('까지')
  if end<0 or re.search(r'[가-힣]{3,}',tail[:end].replace('오전','').replace('오후','')):continue
  before=flat[max(0,m.start()-65):m.start()]
  after=flat[m.end()+end:m.end()+end+55]
  if not CUE.search(before+after):continue
  if re.search(r'합격자 발표|선정 결과',before[-25:]):continue
  y,mo,d=m.groups();h=re.search(r'(오전|오후)?\s*(\d{1,2})(?::(\d\d)|시)',tail[:end]);hour=minute=-1
  if h:
   mer,hh,mm=h.groups();hour=int(hh)+(12 if mer=='오후' and int(hh)<12 else 0);minute=int(mm or 0)
  context=before[-35:]+after[:35]
  role=date_role(before,flat[m.end():m.end()+150])
  out[role].add((int(y or 0),int(mo),int(d),hour,minute))
 return out

def deadline_conflict(a,b):
 l,r=[{k:v for k,v in deadlines(n).items() if k not in {'administrative','benefit','completion'}} for n in (a,b)]
 def equal(x,y):
  return x[1:3]==y[1:3] and (not x[0] or not y[0] or x[0]==y[0]) and (x[3]<0 or y[3]<0 or x[3:]==y[3:])
 for role in l.keys()&r.keys():
  if l[role] and r[role] and not all(any(equal(x,y) for y in r[role]) for x in l[role]):return True
 # Single explicit closing date is still significant if one department calls it recommendation.
 ll=set().union(*l.values()) if l else set();rr=set().union(*r.values()) if r else set()
 for local,other in [(l.get('local',set()),rr),(r.get('local',set()),ll)]:
  if local and other and any(not any(equal(x,y) for y in other) for x in local):return True
 return bool(len(ll)==len(rr)==1 and not equal(next(iter(ll)),next(iter(rr))))

def hard_relation(r):
 a,b=r['left'],r['right'];ta,tb=a['title'],b['title']
 for pattern in [r'(20\d\d)\s*학년도',r'(20\d\d)\s*년',r'(\d+)\s*학기',r'제?\s*(\d+)\s*(?:차|회|기)(?!간)']:
  x,y=[set(re.findall(pattern,t)) for t in (ta,tb)]
  if x and y and x.isdisjoint(y):return 'different_event','연도·학기·회차'
 if all(re.search(r'채용.*(?:대외활동|정보)|대외활동.*정보',t) for t in (ta,tb)):
  x,y=[{m.groups() for m in DATE.finditer(t)} for t in (ta,tb)]
  if x and y and x.isdisjoint(y):return 'different_event','정보 묶음 발행일'
 if deadline_conflict(a,b):return 'related_update','실제 신청/추천/제출 마감'
 # Common boilerplate does not identify which department is lending gowns or administering a degree.
 if all(re.search(r'학위복|학위청구논문|학점\s*인정|졸업\s*사정',t) for t in (ta,tb)):
  ha,hb=[urlsplit(n.get('source_url','')).hostname for n in (a,b)]
  if ha and hb and ha!=hb:return 'needs_context','학과별 행정 대상·조건 확인 필요'
 return '',''

TAGS={
 '행정완료':r'이수\s*기준|학점\s*신청|학점\s*인정|이수\s*인정|졸업\s*요건|선발\s*결과|수상\s*소식|수상하|수강\s*정정|증원|증명서|구독\s*수요조사|클래스\s*변경',
 '작품제출':r'(?:영상|카드뉴스|로고|디자인|포스터|숏폼|UCC|시각화).{0,25}(?:제작|제출|작품|분야|부문)|(?:제출물|출품작|공모분야|공모내용).{0,45}(?:영상|카드뉴스|로고|디자인|포스터|시각화)',
 '제안제출':r'(?:아이디어|정책|슬로건|명칭|네이밍|사업계획).{0,20}(?:제안|공모|제출|경진)|(?:제출물|공모분야).{0,30}(?:아이디어|제안서)',
 '개발제출':r'(?:코드|소스코드|알고리즘|소프트웨어|데이터\s*분석|예측\s*모델).{0,20}(?:개발|제출|구현|경진)',
 '문학제출':r'(?:에세이|수기|독후감|글쓰기|시나리오|시|소설).{0,12}(?:공모|경연|대회)',
 '경쟁선발':r'본선|예선|최우수상|대상.{0,5}만원|심사\s*기준|공모전|경진대회|시상\s*내역',
 '참가교육':r'교육생\s*모집|수강생\s*모집|수강\s*신청|강연|특강|세미나|해외\s*파견|교환학생|교육\s*과정',
 '육성지원':r'액셀러레이팅|사업화\s*지원|멘토링|팀별\s*활동비|고도화\s*지원|제작\s*지원',
 '취업채용':r'구인\s*공고|강사\s*채용|직원\s*채용|채용\s*설명회|취업.{0,8}상담|현장실습.{0,12}모집',
 '도움역할':r'봉사자|봉사단|프로보노|튜터\s*(?:모집|지원)|멘토\s*모집',
}

def purpose(n):
 t=n['title'];b=n.get('body','');lines=re.split(r'[\n;|]',b);found=[]
 for k,p in TAGS.items():
  ss=[s.strip()[:240] for s in lines if re.search(p,s,re.I)]
  if re.search(p,t,re.I):found.extend([k]*4)
  if ss:found.extend([k]*min(3,len(ss)));found.extend(ss[:4])
 return t+' '+t+' '+' '.join(found)+' '+b[:240]

def extra(r):
 a,b=r['left'],r['right'];x,y=deadlines(a),deadlines(b)
 v=[deadline_conflict(a,b),len(x),len(y)]
 for role in ['apply','local','recommend']:
  aa,bb=x.get(role,set()),y.get(role,set());v.extend([bool(aa),bool(bb),len(aa),len(bb),len(aa&bb),bool(aa and bb and aa.isdisjoint(bb))])
 relation,_=hard_relation(r);v.extend([relation==k for k in ['same_event','different_event','related_update','needs_context']]);return v
