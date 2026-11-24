import os
import hashlib,json,os,sys,time,requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit,unquote
from bs4 import BeautifulSoup
from dotenv import load_dotenv
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R));load_dotenv(R/'.env')
from together.plato.auth import http_login_session
from together.plato.http_client import PlatoHttpClient
from together.notices.intelligence.post_editor import validate_article_url
from together.plato.star_cleanup import collect_old_stars
receipts=json.loads((O/'receipts.json').read_text());C=json.loads((O/'context.json').read_text())
posts=json.loads((R/'.runtime/intelligence/published-posts.json').read_text())['posts']
def identity(url):return tuple(validate_article_url(url).values())
index={identity(v['article_url']):v for v in posts}
selected=[v for v in receipts.values() if C['publication_since']<=v.get('updated_at',0)<=C['until']]
http=PlatoHttpClient(PlatoHttpClient._cached_session(R/'.runtime/plato-http-cookies.json'));results=[];errors=[]
def article_get(url):
 for attempt in range(3):
  try:
   http.session.close();http.session=PlatoHttpClient._cached_session(R/'.runtime/plato-http-cookies.json');return http.get(url,timeout=(5,30))
  except requests.HTTPError as exc:
   code=exc.response.status_code if exc.response is not None else 0
   if code not in (401,403,408,429,500,502,503,504) or attempt==2:raise
   exc.response.close()
   if code in (401,403) or (code in (500,502,503,504) and attempt==1):http.session.close();http.session=http_login_session();http._course_html=None
   time.sleep(2)
checkpoint=O/'publication-checkpoint.json'
prior=json.loads(checkpoint.read_text()) if checkpoint.exists() else []
cached={(v['article_url'],v.get('source_url')):v for v in prior if not v.get('error') and v.get('title_verified') is True and v.get('source_link_verified') is True and v.get('indexed_hashes_match') is not False}
try:
 for i,rec in enumerate(selected):
  if (rec['article_url'],rec.get('source_url')) in cached:
   results.append(cached[(rec['article_url'],rec.get('source_url'))]);continue
  row={'article_url':rec.get('article_url'),'source_url':rec.get('source_url'),'state':rec.get('state')}
  try:
   assert rec['state']=='verified'
   with article_get(rec['article_url']) as response:
    response.raise_for_status();soup=BeautifulSoup(response.text,'html.parser')
   title=soup.select_one('.ubboard_view .subject h3');assert title,'Article title unavailable'
   expected_subject=rec['subject']
   if rec.get('duplicate_of'):
    representative=index.get(identity(rec['article_url']));removed=index.get(identity(rec['duplicate_of']))
    assert representative and removed and removed.get('duplicate_of')==rec['article_url']
    assert rec['source_url'] in representative['sources'] and rec['source_url'] in removed['sources']
    primary=next(r for r in receipts.values() if r.get('source_url')==representative['notice']['source_url'] and identity(r.get('article_url',''))==identity(rec['article_url']) and not r.get('duplicate_of'))
    assert primary['state']=='verified';expected_subject=primary['subject'];row['source_alias_verified']=True;row['deleted_duplicate_url']=rec['duplicate_of']
   content=soup.select_one('.ubboard_view')
   body=content.select_one('.text_to_html, .contents, .content') if content else None
   body=body or content
   row['body_text_sha256']=hashlib.sha256(body.get_text(' ',strip=True).encode()).hexdigest() if body else None
   row['body_images']=len(body.select('img')) if body else 0
   row['body_read']=body is not None
   assert row['body_read'],'Article body unavailable'
   row['title_verified']=expected_subject.lstrip('⭐').strip() in title.get_text(' ',strip=True).lstrip('⭐').strip()
   if not row['title_verified']:
    proof=R/'.runtime/star-cleanup'/(validate_article_url(rec['article_url'])['post_id']+'.json')
    if proof.exists():
     saved=json.loads(proof.read_text())
     row['prior_edit_title_matches']=saved.get('verified') is True and saved.get('after',{}).get('subject','').lstrip('⭐').strip()==title.get_text(' ',strip=True).lstrip('⭐').strip()
     row['title_verified']=row['prior_edit_title_matches']
   record=index.get(identity(rec['article_url']));expected=record.get('file_hashes',{}) if record else {}
   source=record['notice']['source_url'] if record else rec['source_url']
   row['source_link_verified']=source in soup.get_text(' ',strip=True) or soup.find('a',href=source) is not None
   marker='/mod_ubboard/attachment/'+validate_article_url(rec['article_url'])['post_id']+'/'
   links={a.get('href') or a.get('src') for a in soup.select('a[href],img[src]')};actual={}
   for url in links:
    parts=urlsplit(url)
    if parts.netloc!='dev-plato.pusan.ac.kr' or marker not in parts.path:continue
    digest=hashlib.sha256();name=unquote(parts.path.rsplit('/',1)[-1])
    # Binary files do not need the HTML login detector, which decodes the whole
    # stream. A login/error response will fail its expected byte hash below.
    with http.session.get(url,timeout=(5,30),stream=True,allow_redirects=True) as stream:
     assert stream.status_code==200,'Published attachment inaccessible'
     for chunk in stream.iter_content(65536):digest.update(chunk)
    actual[name]=digest.hexdigest()
   row['attachment_count']=len(actual);row['indexed_hashes_match']=actual==expected if record else None
   if not row['title_verified'] or not row['source_link_verified'] or row['indexed_hashes_match'] is False:errors.append(row.copy())
  except Exception as exc:row['error']=type(exc).__name__;errors.append(row.copy())
  results.append(row)
  temporary=checkpoint.with_suffix('.pending');temporary.write_text(json.dumps(results,ensure_ascii=False));temporary.chmod(0o600);temporary.replace(checkpoint)
  time.sleep(.7)
  if (i+1)%50==0:
   (O/'publications-progress.json').write_text(json.dumps({'checked':i+1,'expected':len(selected),'issues':len(errors)}));print(i+1,flush=True)
 stars=[]
 http.session.close();http.session=http_login_session();http._course_html=None
 for name in ('교육/특강/프로그램','[공모전] 공학/IT/SW','[공모전] 아이디어/기획','[공모전] 미술/디자인/건축','[공모전] 문학/수기/에세이','[공모전] 기타','봉사활동','서포터즈','취업 정보','장학금','기타 공지사항'):
  try:
   url=http.board_url(name);urls,pages=collect_old_stars(http,url,datetime.fromtimestamp(C['until']).date().isoformat());stars.append({'board':name,'search_pages':pages,'stars_before_current_day':len(urls)})
  except Exception as exc:stars.append({'board':name,'error':type(exc).__name__})
finally:http.session.close()
errors=[row for row in results if row.get('error') or row.get('title_verified') is False or row.get('source_link_verified') is False or row.get('indexed_hashes_match') is False]
proof={'checked_at':datetime.now().astimezone().isoformat(),'expected_receipts':len(selected),'checked':len(results),'errors':errors,'indexed_attachment_sets_verified':sum(row.get('indexed_hashes_match') is True for row in results),'attachment_files_hashed':sum(row.get('attachment_count',0) for row in results),'star_search':stars,'post_or_send_requests':0,'results':results}
p=O/'publications.json';p.write_text(json.dumps(proof,ensure_ascii=False,indent=2));p.chmod(0o600);print(json.dumps({k:v for k,v in proof.items() if k not in ('results','errors')},ensure_ascii=False))
