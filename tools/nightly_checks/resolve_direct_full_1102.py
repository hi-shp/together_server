import os
"""Enumerate actual outbound message records, not the latest thread preview."""
import json,re,sys,time,hashlib,collections
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);F=Path('/home/ubuntu/together-final-review-20261026');sys.path.insert(0,str(R));load_dotenv(R/'.env')
from together.plato.auth import http_login_session
from together.plato.http_client import PlatoHttpClient
index=json.loads((O/'native-message-index-private.json').read_text());proof=json.loads((O/'message-reconciliation-private.json').read_text());groups=json.loads((O/'native-message-groups-private.json').read_text());intents=json.loads((O/'message-intents-snapshot-private.json').read_text());since=proof['since'];until=proof['until']
path=O/'direct-native-records-private.json';state=json.loads(path.read_text()) if path.exists() else {'threads':{},'records':{}}
def save(name,v):
 p=O/name;p.write_text(json.dumps(v,ensure_ascii=False,indent=2));p.chmod(0o600)
def ev(d,u,t):return hashlib.sha256(json.dumps(['message',d+'|'+u+'|'+t],ensure_ascii=False).encode()).hexdigest()
http=PlatoHttpClient(http_login_session(),O/'direct-cookies-private.json')
try:
 for key,v in index['entries'].items():
  if v['kind']!='direct' or key in state['threads']:continue
  with http.get(v['url'],timeout=(7,35)) as response:response.raise_for_status();soup=BeautifulSoup(response.text,'html.parser')
  total=0;within=0
  for n in soup.select('.message'):
   content=n.select_one('.content');cancel=n.select_one('.message_send_cancel')
   if content is None:continue
   if cancel is None and '투게더 본인 순서 점검 20261028_094254' not in content.get_text():continue
   content=content.get_text(' ',strip=True);m=re.search(r'✔ \[(.*?)\]\s*새로운 공지가 등록되었습니다\.\s*제목:\s*(.*?)\s+(https?://\S+)',content,re.S)
   if not m:continue
   total+=1;day=n.find_previous('h4',class_='day');time_node=n.select_one('.time');dm=re.search(r'(\d{4})년 (\d{1,2})월 (\d{1,2})일',day.get_text()) if day else None
   tm=re.search(r'(\d{1,2}):(\d{2})\s*(오전|오후)',time_node.get('title','')) if time_node else None
   if not dm or not tm:raise ValueError('Native direct timestamp missing')
   year,month,date=map(int,dm.groups());hour,minute,ampm=tm.groups();hour=int(hour)%12+(12 if ampm=='오후' else 0);stamp=datetime(year,month,date,hour,int(minute)).timestamp()
   if not since-60<=stamp<=until:continue
   within+=1;dep,title,url=m.groups();event=ev(dep,url.strip(),title.strip());anchor=n.select_one('a[name]');native=anchor['name'] if anchor else hashlib.sha256((key+'|'+str(stamp)+'|'+content).encode()).hexdigest()
   state['records'][key+':'+native]={'event':event,'identity':{'department':dep,'title':title.strip(),'source_url':url.strip()},'uid':v['native_id'],'native_message_id':native,'stamp':stamp,'self_test':'투게더 본인 순서 점검 20261028_094254' in content}
  state['threads'][key]={'all_outbound_notices':total,'in_period':within,'verified_at':datetime.now().astimezone().isoformat()};save(path.name,state);time.sleep(.2)
  print(json.dumps({'direct_threads':len(state['threads']),'native_records_in_period':len(state['records'])}),flush=True)
 byevent=collections.defaultdict(list)
 for key,v in index['entries'].items():
  if v['kind']=='group' and v['event'] and v['stamp']<=until:byevent[v['event']].append({'uids':groups[key]['uids'],'identity':v['identity'],'native_key':key,'body_matches':groups[key]['body_identity_matches']})
 selfkey=hashlib.sha256(json.dumps(['message',json.loads((F/'legacy-self-test-request.json').read_text())['token']+'-message'],ensure_ascii=False).encode()).hexdigest()
 for key,v in state['records'].items():
  event=selfkey if v['self_test'] else v['event']
  if v['stamp']<since and event not in intents:continue
  byevent[event].append({'uids':[v['uid']],'identity':v['identity'],'native_key':key,'body_matches':True})
 results=proof['results']
 for event,intent in intents.items():
  found=byevent.get(event,[]);counts=collections.Counter(u for row in found for u in row['uids']);expected=set(intent['uids']);actual=set(counts)
  results[event]={'verified':bool(found) and actual==expected and all(r['body_matches'] for r in found),'identity':found[0]['identity'] if found else results[event]['identity'],'expected':len(expected),'actual_union':len(actual),'native_groups':len(found),'missing_uids':sorted(expected-actual),'unexpected_uids':sorted(actual-expected),'duplicate_uids':sorted(u for u,n in counts.items() if n>1),'group_ids':[r['native_key'] for r in found]}
 proof['results']=results;proof['native_not_in_intents']=[{'event':k,'identity':v[0]['identity'],'native_records':len(v)} for k,v in byevent.items() if k not in intents];proof['direct_threads_complete']=len(state['threads']);save('message-reconciliation-private.json',proof)
 safe={'checked_at':datetime.now().astimezone().isoformat(),'since':since,'until':until,'expected_events':len(results),'verified_events':sum(v['verified'] for v in results.values()),'direct_threads':len(state['threads']),'direct_actual_records':len(state['records']),'unverified':[{'event':k,'identity':v['identity'],'expected':v['expected'],'actual':v['actual_union'],'missing_count':len(v['missing_uids'])} for k,v in results.items() if not v['verified']],'duplicates':[{'event':k,'identity':v['identity'],'overlap':len(v['duplicate_uids'])} for k,v in results.items() if v['duplicate_uids']],'native_not_in_intents':proof['native_not_in_intents'],'new_sends':0}
 save('message-final-reconciliation.json',safe);print(json.dumps(safe,ensure_ascii=False),flush=True)
finally:http.close()
