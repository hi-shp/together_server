import os
"""Inventory public PNU navigation and notice boards without changing production."""
import concurrent.futures as futures
from collections import Counter
from datetime import datetime
import csv,gzip,hashlib,html,json,re,threading,time
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
import requests,urllib3
from bs4 import BeautifulSoup
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
BASE=Path(__file__).resolve().parent
OUT=Path(os.getenv('PNU_DISCOVERY_DIR') or BASE/'pnu-discovery');OUT.mkdir(exist_ok=True)
CACHE=OUT/'html';CACHE.mkdir(exist_ok=True)
existing=list(csv.DictReader((Path(os.getenv('PNU_EXISTING_PAGES') or BASE/'discovery-live-pages.csv')).open(encoding='utf-8-sig')))
LOCK=threading.Lock();HOSTLOCK={};LAST={};LOCAL=threading.local()
KW=re.compile(r'공지|알림|새소식|학사|장학|취업|채용|행사|모집|교육안내|일반안내|대학원안내|notice|announcement|news|events?|seminar|scholar|recruit',re.I)
SKIP=re.compile(r'로그인|로그아웃|회원가입|개인정보|저작권|사이트맵|sitemap|login|logout|privacy',re.I)
GENERIC={'www','his','pusan','busan','sites','bbs','kor','eng','web','cms'}
def norm(u):
 u=html.unescape(u.strip())
 if u.startswith(('http://https://','https://https://','http://http://')):u=u.split('://',1)[1]
 p=urlsplit(u)
 if p.scheme not in ('http','https') or not p.hostname:return None
 if not(p.hostname.endswith('.pusan.ac.kr') or p.hostname.endswith('.busan.ac.kr') or p.hostname in ('pusan.ac.kr','busan.ac.kr')):return None
 if re.search(r'\.(?:pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip|jpg|png|gif|svg|css|js|ico|mp4)(?:$|[?])',p.path,re.I):return None
 if re.search(r'logout|delete|download|action\.php|apply\.php',p.path,re.I):return None
 query=p.query
 if re.search(r'/[^/]+/\d+/subview\.?\.do$',p.path):
  query=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k not in ('enc','page','searchType','srchColumn','srchWrd')])
 return urlunsplit(('https' if p.scheme.lower()=='http' else p.scheme.lower(),p.netloc.lower(),p.path,query,''))
def site(u):
 p=urlsplit(u);parts=[x for x in p.path.split('/') if x]
 if len(parts)==1 and p.hostname.split('.')[0] in ('his','ihis','prof') and '.' not in parts[0]:key=parts[0]
 elif len(parts)>=2 and parts[0] in ('sites','bbs'):key=parts[1]
 elif len(parts)>=2 and (parts[-1] in ('index.do','subview.do','subview..do') or re.fullmatch(r'\d+',parts[1])):key=parts[0]
 else:key=p.hostname.split('.')[0]
 if key.lower() in GENERIC:return None
 if not re.fullmatch(r'[A-Za-z0-9_-]+',key):return None
 if parts and (parts[0] in ('sites','bbs') or parts[0]==key):
  root=f'https://{p.netloc}/{key}/index.do'
 else:root=f'https://{p.netloc}/'
 return key.lower(),root
def cachekey(u):return hashlib.sha256(u.encode()).hexdigest()

def route_page(m,data):
 if not data:return m,data
 soup=BeautifulSoup(data,'html.parser')
 rendered=str(soup)
 if not any(x in rendered for x in ('pnuDomainChk.do','http-equiv="refresh"',"http-equiv='refresh'")):return m,data
 if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
 visited={m.get('final_url',m['url'])}
 chain=[]
 for _ in range(4):
  soup=BeautifulSoup(data,'html.parser');text=str(soup);base=m.get('final_url',m['url']);target=None
  for endpoint in ('/his/pnuDomainChk.do','/pnuDomain/pnuDomainChk.do'):
   if endpoint in text:
    try:
     with LOCAL.session.post(urljoin(base,endpoint),timeout=(5,18),verify=False) as response:
      if response.status_code==200 and response.text.strip():
       value=response.json();target=norm(value.get('siteUrl') or value.get('siteId') or '')
       chain.append({'lookup':urljoin(base,endpoint),'target':target})
    except (requests.RequestException,ValueError):pass
    break
  if not target:
   refresh=soup.find('meta',attrs={'http-equiv':re.compile('refresh',re.I)})
   match=re.search(r'url\s*=\s*(.+)',refresh.get('content',''),re.I) if refresh else None
   if match:
    candidate=norm(urljoin(base,match.group(1).strip(" '\"")))
    if candidate and not re.search(r'/sites/(?:his|ihis)/index',candidate):target=candidate
  if not target or target in visited:
   m['routing_unresolved']=True;break
  visited.add(target)
  try:
   with LOCAL.session.get(target,timeout=(5,18),verify=False) as response:
    response.raise_for_status();data=response.content;m['final_url']=response.url
  except requests.RequestException as exc:
   m['routing_error']=type(exc).__name__;break
  if 'pnuDomainChk.do' not in str(BeautifulSoup(data,'html.parser')):break
 m['routing_checked']=True;m['routing_chain']=chain
 return m,data

def fetch(u):
 key=cachekey(u);meta=CACHE/(key+'.json');raw=CACHE/(key+'.gz')
 if meta.exists():
  
  try:m=json.loads(meta.read_text(encoding='utf-8'))
  except (ValueError,OSError):m={}
  fresh=raw.exists() and 0 <= time.time()-raw.stat().st_mtime <= 6*3600
  if fresh and m.get('status')==200 and m.get('url')==u and not m.get('routing_unresolved'):
   try:
    with gzip.open(raw,'rb') as h:data=h.read()
   except (OSError,EOFError):data=b''
   if not m.get('routing_checked'):
    m,data=route_page(m,data)
    if m.get('routing_checked'):
     with gzip.open(raw,'wb') as h:h.write(data)
     meta.write_text(json.dumps(m,ensure_ascii=False),encoding='utf-8')
   if data:return m,data
 p=urlsplit(u)
 with LOCK: gate=HOSTLOCK.setdefault(p.hostname,threading.Lock())
 try:
  with gate:
   with LOCK:
    gap=0.4-(time.monotonic()-LAST.get(p.hostname,0))
   if gap>0:time.sleep(gap)
   if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
   with LOCAL.session.get(u,timeout=(5,18),verify=False,headers={'User-Agent':'Together-PNU-Notice-Audit/1.0'},allow_redirects=True) as r:
    data=r.content;ctype=r.headers.get('Content-Type','')
    m={'url':u,'final_url':r.url,'status':r.status_code,'content_type':ctype,'checked_at':datetime.now().astimezone().isoformat()}
    if 'html' not in ctype.lower() and not data.lstrip().startswith((b'<',b'<!')):data=b''
   with LOCK:LAST[p.hostname]=time.monotonic()
  if data:
   m,data=route_page(m,data)
   with gzip.open(raw,'wb') as h:h.write(data)
 except requests.RequestException as exc:m={'url':u,'error':type(exc).__name__,'checked_at':datetime.now().astimezone().isoformat()};data=b''
 if not data:raw.unlink(missing_ok=True)
 meta.write_text(json.dumps(m,ensure_ascii=False),encoding='utf-8')
 return m,data
def links(data,base):
 soup=BeautifulSoup(data,'html.parser');found={}
 for a in soup.select('a[href],option[value]'):
  href=a.get('href') or a.get('value') or ''
  clicked=a.get('onclick','') or a.get('data-url','')
  if clicked:
   match=re.search(r'''(?:https?://)[^'"\s)]+''',clicked)
   if match:href=match.group(0)
  if href.lower().startswith('javascript:'):
   m=re.search(r"""['"]((?:https?://|/)[^'"]+)['"]""",href);href=m.group(1) if m else ''
  u=norm(urljoin(base,href))
  if u:found[u]=' '.join(a.get_text(' ',strip=True).split())[:180]
 return soup,found
SITES={};DIRECTORIES={};CANDIDATES={};RESULTS=[];VISITED_HOME=set();DIRVIS=set()
def add_site(u,label,origin):
 item=site(u)
 if not item:return
 key,root=item
 record=SITES.setdefault(key,{'key':key,'url':root,'labels':[],'origins':[],'status':'pending'})
 if label and label not in record['labels']:record['labels'].append(label)
 if origin not in record['origins']:record['origins'].append(origin)
def add_candidate(u,label,origin,key=''):
 if SKIP.search(label):return
 r=CANDIDATES.setdefault(u,{'url':u,'labels':[],'origins':[],'site_key':key})
 if label and label not in r['labels']:r['labels'].append(label)
 if origin not in r['origins']:r['origins'].append(origin)
def absorb(m,data,origin,key='',central=False):
 if not data:return None,{}
 soup,ls=links(data,m.get('final_url',m['url']))
 for u,label in ls.items():
  add_site(u,label,origin)
  if central and ('www.pusan.ac.kr/kor/CMS/' in u or 'www.busan.ac.kr/kor/CMS/' in u):
   if any(x in u for x in ('Contents/Contents.do','OrganizationMgr/view.do','SiteMap','Research')):
    DIRECTORIES.setdefault(u,label)
  if KW.search(label) and not SKIP.search(label):
   if re.search(r'subview\.?\.do|artclList|list\.|board|notice|community|bbs|Board',u,re.I) and 'artclView.do' not in u and not re.search(r'(?:[?&])mode=view',u):
    add_candidate(u,label,origin,key)
 return soup,ls
def save():
 summary={'at':datetime.now().astimezone().isoformat(),'existing_rows':len(existing),'directories':len(DIRVIS),'sites':len(SITES),'sites_visited':len(VISITED_HOME),'site_states':dict(Counter(x['status'] for x in SITES.values())),'candidate_urls':len(CANDIDATES),'board_results':dict(Counter(x.get('result','') for x in RESULTS))}
 (OUT/'progress.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
 for name,data in [('sites.json',SITES),('directories.json',DIRECTORIES),('candidates.json',CANDIDATES),('boards.json',RESULTS)]:
  (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(summary,ensure_ascii=False),flush=True)
if os.getenv('PNU_RESUME_SNAPSHOT') == '1':
 # Retain this audit's visited pages while adding the complete official registry.
 def prior(name): return json.loads((OUT/name).read_text(encoding='utf-8'))
 SITES.update(prior('sites.json'));DIRECTORIES.update(prior('directories.json'))
 CANDIDATES.update(prior('candidates.json'));RESULTS.extend(prior('boards.json'))
 VISITED_HOME.update(k for k,v in SITES.items() if v['status']!='pending')
 if len(DIRECTORIES)==prior('progress.json')['directories']:DIRVIS.update(DIRECTORIES)
for row in existing:
 add_site(row['page_url'],row['department'],'existing_csv')
 add_candidate(row['page_url'],row['department'],'existing_csv',site(row['page_url'])[0] if site(row['page_url']) else '')
for u in ['https://www.pusan.ac.kr/kor/CMS/Contents/Contents.do?mCode=MN003','https://www.pusan.ac.kr/kor/CMS/Contents/Contents.do?mCode=MN298','https://www.pusan.ac.kr/kor/CMS/OrganizationMgr/view.do?mCode=MN168']:
 DIRECTORIES[u]='central_official'
extras=OUT/'extra-seeds.json'
if extras.exists():
 for row in json.loads(extras.read_text(encoding='utf-8')):
  u=norm(row['url'])
  if u:add_site(u,row.get('name',''),'official_his_registry')
for u in ['https://isrc.pusan.ac.kr/isrc/index.do','https://his.pusan.ac.kr/iaom/72058/subview.do','https://ilb.pusan.ac.kr/iLB/39985/subview.do','https://iee.pusan.ac.kr/ieti/34374/subview.do','https://riit-re.pusan.ac.kr/riit-re/index.do','https://his.pusan.ac.kr/bk21four-mp/60099/subview.do','https://his.pusan.ac.kr/pncc/index.do','https://his.pusan.ac.kr/pnuchinese/index.do']:
 add_site(u,'','official_search')

with futures.ThreadPoolExecutor(max_workers=3) as pool:
 while True:
  pending=[u for u in DIRECTORIES if u not in DIRVIS]
  if not pending:break
  for u,(m,data) in zip(pending,pool.map(fetch,pending)):
   DIRVIS.add(u);absorb(m,data,u,central=True)
  save()
 while True:
  pending=[(k,x['url']) for k,x in SITES.items() if k not in VISITED_HOME]
  if not pending:break
  batch=pending[:36]
  for (key,u),(m,data) in zip(batch,pool.map(fetch,[u for _,u in batch])):
   VISITED_HOME.add(key);r=SITES[key];r.update(response=m)
   if data and m.get('status')==200:
    soup,ls=absorb(m,data,u,key);r['title']=soup.title.get_text(' ',strip=True) if soup.title else '';r['status']='routing_unresolved' if m.get('routing_unresolved') else 'ok'
    for target,label in ls.items():
     if re.search(r'사이트맵|sitemap',label,re.I) and urlsplit(target).hostname==urlsplit(m.get('final_url',u)).hostname:
      mm,dd=fetch(target);absorb(mm,dd,target,key)
   else:r['status']=m.get('error') or 'http_'+str(m.get('status'))
  save()
 # Include existing rows to establish canonical board identity before appending.
 done={r['url'] for r in RESULTS}
 ordered=[u for u in CANDIDATES if u not in done]
 for offset in range(0,len(ordered),30):
  batch=ordered[offset:offset+30]
  for u,(m,data) in zip(batch,pool.map(fetch,batch)):
   record={**CANDIDATES[u],**m};record['result']='unavailable'
   if data and m.get('status')==200:
    soup,ls=links(data,m.get('final_url',u));body=soup.get_text(' ',strip=True)
    record['page_title']=soup.title.get_text(' ',strip=True) if soup.title else ''
    table=soup.find('tbody');record['tbody']=table is not None
    numeric=[];article_links=[];examples=[]
    if table:
     for tr in table.select('tr'):
      num=tr.select_one('td._artclTdNum,td.number,td.num,td.td-num')
      title=tr.select_one('td._artclTdTitle a[href],td.subject a[href],td.td-title a[href]')
      if num and num.get_text(strip=True).isdigit():
       numeric.append(int(num.get_text(strip=True)))
       if title:
        raw_detail=urljoin(m.get('final_url',u),title['href'])
        detail=raw_detail if norm(raw_detail) else None
        if detail:article_links.append(detail);examples.append({'number':numeric[-1],'title':title.get_text(' ',strip=True),'url':detail})
    record['max_number']=max(numeric) if numeric else None
    record['numeric_count']=len(numeric);record['examples']=examples[:3]
    record['result']='numeric_notice_board' if numeric and article_links else 'empty_or_pinned_board' if table else 'not_board_or_changed'
    if any(t in body for t in ('메뉴이(가) 존재 하지 않습니다','사용중지된 싱글 모듈입니다')):record['result']='school_disabled'
    ids=[]
    for link in article_links:
     import base64
     encoded=dict(parse_qsl(urlsplit(link).query)).get('enc')
     if encoded:
      try:
       from urllib.parse import unquote
       decoded=unquote(base64.b64decode(encoded+'='*(-len(encoded)%4)).decode())
       link=decoded.split('|@@|')[-1]
      except (ValueError,UnicodeError):pass
     mt=re.search(r'/bbs/([^/]+)/(\d+)/\d+/artclView\.do',link)
     if mt:ids.append('k2web:'+mt.group(1)+':'+mt.group(2))
    if ids:record['canonical_board']=Counter(ids).most_common(1)[0][0]
    else:record['canonical_board']=m.get('final_url',u)
   RESULTS.append(record)
  save()
save()

