import os
"""Read-only complete native history scan and exact per-recipient reconciliation."""
import json,sys,re,hashlib,sqlite3,time,collections
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin,urlsplit,parse_qs
from bs4 import BeautifulSoup
from dotenv import load_dotenv
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));F=Path('/home/ubuntu/together-final-review-20261026');OLD=F/'weekly/20261101';O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R));load_dotenv(R/'.env')
from together.plato.auth import http_login_session
from together.plato.http_client import PlatoHttpClient
BASE='https://dev-plato.pusan.ac.kr/local/ubmessage/';C=json.loads((O/'context.json').read_text());since=C['message_since']
def save(name,v):
 p=O/name;p.write_text(json.dumps(v,ensure_ascii=False,indent=2));p.chmod(0o600)
def ev(k):return hashlib.sha256(json.dumps(['message',k],ensure_ascii=False).encode()).hexdigest()
http=PlatoHttpClient(http_login_session(),O/'resolve-cookies-private.json')
def get(url):
 for i in range(2):
  try:
   with http.get(url,timeout=(7,35)) as r:r.raise_for_status();soup=BeautifulSoup(r.text,'html.parser')
   time.sleep(.2);return soup
  except Exception:
   if i:raise
   time.sleep(3)
def identify(body):
 m=re.search(r'✔ \[(.*?)\]\s*새로운 공지가 등록되었습니다\.\s*제목:\s*(.*?)\s+(https?://\S+)',body,re.S)
 if not m:return None
 dep,title,url=m.groups();return {'department':dep,'title':title.strip(),'source_url':url.strip()}
state_path=O/'native-message-index-private.json';state=json.loads(state_path.read_text()) if state_path.exists() else {'started_at':datetime.fromtimestamp(C['until']).astimezone().isoformat(),'since':since,'pages':0,'entries':{},'complete':False}
try:
 if not state['complete']:
  page=state['pages']+1
  while True:
   soup=get(BASE+'index.php?mc=157301&page='+str(page));anchors=[a for a in soup.select('a[href]') if urlsplit(urljoin(BASE,a['href'])).path in ('/local/ubmessage/gmessage.php','/local/ubmessage/message.php') and a.select_one('.msg')]
   dates=[]
   for a in anchors:
    url=urljoin(BASE,a['href']);parts=urlsplit(url);uid=parse_qs(parts.query).get('id',[''])[0];kind='group' if parts.path.endswith('/gmessage.php') else 'direct';date=a.select_one('.time').get_text(' ',strip=True);stamp=datetime.fromisoformat(date).timestamp();dates.append(stamp)
    if stamp<since-60:continue
    body=a.select_one('.msg').get_text(' ',strip=True);ident=identify(body);key=ev(ident['department']+'|'+ident['source_url']+'|'+ident['title']) if ident else None
    state['entries'][kind+':'+uid]={'url':url,'kind':kind,'native_id':uid,'date':date,'stamp':stamp,'body':body,'identity':ident,'event':key}
   state['pages']=page;save(state_path.name,state)
   if page%10==0:print(json.dumps({'stage':'index','pages':page,'native_conversations':len(state['entries'])}),flush=True)
   if (dates and max(dates)<since-60) or not anchors:
    state['complete']=True;state['finished_at']=datetime.now().astimezone().isoformat();save(state_path.name,state);break
   page+=1
 # Freeze intent snapshot only after history scan; later sends excluded by time cut.
 until=C['until']
 with sqlite3.connect('file:'+str(R/'.runtime/notification-deliveries.sqlite3')+'?mode=ro',uri=True) as db:
  intents={}
  for event,uid,status,did,updated in db.execute("SELECT event,uid,state,delivery_id,updated FROM delivery_recipients WHERE kind='message' AND updated>=? AND updated<=?",(since,until)):
   i=intents.setdefault(event,{'uids':set(),'states':set(),'delivery_ids':set()});i['uids'].add(uid);i['states'].add(status);i['delivery_ids'].add(did)
 save('message-intents-snapshot-private.json',{k:{**v,'uids':sorted(v['uids']),'states':sorted(v['states']),'delivery_ids':sorted(v['delivery_ids'])} for k,v in intents.items()})
 missing=json.loads((O/'unverified-intents-private.json').read_text());identities={v['event']:v['identity'] for v in missing if v['identity']}
 for v in state['entries'].values():
  if v['event'] in intents:identities[v['event']]=v['identity']
 save('native-recovered-identities.json',{k:v for k,v in identities.items() if k in {x['event'] for x in missing}})
 groups_path=O/'native-message-groups-private.json';groups=json.loads(groups_path.read_text()) if groups_path.exists() else {}
 # Each run reads its own current-period recipient sets; no old checkpoint dependency.
 oldverified=set() # Freshly read every current-period recipient set.
 occurrences=collections.Counter(v['event'] for v in state['entries'].values() if v['event'])
 # Reuse the 743 already inspected actual recipient sets. Read every newly
 # unverified, extra or repeated native conversation, without repeating a healthy audit.
 for key,v in state['entries'].items():
  if key in groups:continue
  if v['event'] in oldverified and occurrences[v['event']]==1:
   groups[key]={'uids':sorted(intents[v['event']]['uids']) if v['event'] in intents else [],'body_identity_matches':True,'self_test_present':False,'direct_body':None,'verification':'prior_full_native_recipient_proof_with_unique_current_index_entry'}
   continue
  soup=get(v['url']);uids=set()
  for a in soup.select('.group_message_users a[href], .message_send_cancel a[href]'):
   p=urlsplit(a['href']);q=parse_qs(p.query)
   if q.get('touser',[''])[0].isdigit():uids.add(q['touser'][0])
   if p.path=='/local/ubmessage/message.php' and q.get('id',[''])[0].isdigit():uids.add(q['id'][0])
  if v['kind']=='direct':uids={v['native_id']}
  body=soup.get_text(' ',strip=True)
  groups[key]={'uids':sorted(uids),'body_identity_matches':bool(v['identity'] and v['identity']['title'] in body and v['identity']['source_url'] in body and '✔ ['+v['identity']['department']+']' in body),'self_test_present':'투게더 본인 순서 점검 20261028_094254' in body,'direct_body':body if v['kind']=='direct' else None,'verification':'fresh_full_native_recipient_set'}
  if len(groups)%10==0:save(groups_path.name,groups)
  if len(groups)%50==0:print(json.dumps({'stage':'members','checked':len(groups),'total':len(state['entries'])}),flush=True)
 save(groups_path.name,groups)
 byevent=collections.defaultdict(list)
 for key,v in state['entries'].items():
  if v['event'] and v['stamp']<=until:byevent[v['event']].append((key,v,groups[key]))
 results={};unmatched=[]
 token=json.loads((F/'legacy-self-test-request.json').read_text())['token'];selfevent=ev(token+'-message')
 for e,intent in intents.items():
  entries=byevent.get(e,[]);counter=collections.Counter(uid for _,_,g in entries for uid in g['uids']);actual=set(counter);expected=intent['uids']
  if e==selfevent:
   entries=[(k,v,g) for k,g in groups.items() for v in [state['entries'][k]] if g['self_test_present'] and v['kind']=='direct' and {v['native_id']}==expected]
   actual=set().union(*(set(g['uids']) for _,_,g in entries)) if entries else set();counter=collections.Counter(actual)
  good=actual==expected and bool(entries) and all(g['body_identity_matches'] or e==selfevent for _,_,g in entries)
  results[e]={'verified':good,'identity':identities.get(e) or (entries[0][1]['identity'] if entries else None),'expected':len(expected),'actual_union':len(actual),'native_groups':len(entries),'missing_uids':sorted(expected-actual),'unexpected_uids':sorted(actual-expected),'duplicate_uids':sorted(u for u,n in counter.items() if n>1),'group_ids':[v['native_id'] for _,v,_ in entries]}
 for e,entries in byevent.items():
  if e not in intents:unmatched.append({'event':e,'identity':entries[0][1]['identity'],'groups':[v['native_id'] for _,v,_ in entries]})
 save('message-reconciliation-private.json',{'results':results,'native_not_in_intents':unmatched,'since':since,'until':until})
 safe={'checked_at':datetime.now().astimezone().isoformat(),'since':since,'until':until,'index_pages':state['pages'],'history_scan_complete':state['complete'],'native_conversations':len(groups),'expected_events':len(intents),'verified_events':sum(v['verified'] for v in results.values()),'unverified_events':sum(not v['verified'] for v in results.values()),'duplicate_event_candidates':sum(bool(v['duplicate_uids']) for v in results.values()),'native_not_in_intents':len(unmatched),'previous_nine':[{'event':v['event'],**{k:a for k,a in results.get(v['event'],{}).items() if k not in ('missing_uids','unexpected_uids','duplicate_uids','group_ids')},'missing_count':len(results.get(v['event'],{}).get('missing_uids',[])),'duplicate_recipient_count':len(results.get(v['event'],{}).get('duplicate_uids',[]))} for v in missing],'post_send_requests':0}
 save('message-reconciliation.json',safe);print(json.dumps(safe,ensure_ascii=False),flush=True)
finally:http.close()
