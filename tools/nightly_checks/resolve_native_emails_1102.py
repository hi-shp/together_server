import os
"""Read-only native email history in both directions; never submit send forms."""
import json,sys,sqlite3,re,hashlib,time,collections
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit,parse_qs
from bs4 import BeautifulSoup
from dotenv import load_dotenv
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));F=Path('/home/ubuntu/together-final-review-20261026');OLD=F/'weekly/20261101';O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R));load_dotenv(R/'.env')
from together.plato.auth import http_login_session
from together.plato.http_client import PlatoHttpClient
from together.plato.notifications import parse_email_history,parse_send_form,normalize
from together.plato.email_dispatch import _HistoryLinks
from together.operations.nightly_email_clock import dates,calibrate
C=json.loads((O/'context.json').read_text());since=C['email_since'];until=C['until']
def save(name,v):
 p=O/name;p.write_text(json.dumps(v,ensure_ascii=False,indent=2));p.chmod(0o600)
with sqlite3.connect('file:'+str(R/'.runtime/notification-deliveries.sqlite3')+'?mode=ro',uri=True) as db:
 deliveries=[dict(zip(('id','recipients','seconds','detail','updated'),row)) for row in db.execute("SELECT id,recipients,seconds,detail,updated FROM deliveries WHERE kind='email' AND state='sent' AND updated>=? AND updated<=?",(since,until))]
 ledger={}
 for did,event,uid in db.execute("SELECT delivery_id,event,uid FROM delivery_recipients WHERE kind='email'"):
  v=ledger.setdefault(did,{'event':event,'uids':set()});v['uids'].add(uid)
identities={}
with sqlite3.connect('file:'+str(R/'.runtime/intelligence/observations.sqlite3')+'?mode=ro',uri=True) as db:
 for dep,url,title in db.execute('SELECT department,source_url,title FROM notices'):
  k=hashlib.sha256(json.dumps(['email',dep+'|'+url+'|'+title],ensure_ascii=False).encode()).hexdigest();identities[k]={'subject':'✔ ['+dep+'] '+title,'source_url':url}
http=PlatoHttpClient(http_login_session(),O/'email-cookies-private.json')
def get(url):
 with http.get(url,timeout=(7,35)) as r:r.raise_for_status();text=r.text
 time.sleep(.2);return text
records={};queue=[0];visited=set();wanted={r['detail'].removeprefix('email_log:') for r in deliveries if re.fullmatch(r'email_log:[0-9]+',r['detail'])};directory={}
try:
 text=get('https://dev-plato.pusan.ac.kr/local/ubmessage/send.php?id=157301&lang=ko');_,targets=parse_send_form(text,'message','https://dev-plato.pusan.ac.kr/local/ubmessage/send.php?id=157301&lang=ko')
 for target in targets:
  m=re.search(r'\((\d{7,12})\)',target['title'] or target['text'])
  if m:directory[target['id']]=m[1]
 while queue:
  page=queue.pop(0)
  if page in visited:continue
  visited.add(page);text=get('https://dev-plato.pusan.ac.kr/local/ubmessage/email_log.php?id=157301&page='+str(page));soup=BeautifulSoup(text,'html.parser')
  school_dates=dates(text)
  for row in parse_email_history(text):records[row['eid']]={**row,'student_ids':sorted(row['student_ids']),'native_clock_raw':school_dates.get(row['eid'])}
  links=_HistoryLinks();links.feed(text)
  for url in links.urls:
   n=int(parse_qs(urlsplit(url).query).get('page',[0])[0])
   if n not in visited and n not in queue:queue.append(n)
  queue.sort()
  # Need all rows down through earliest requested receipt, not just a title match.
  # Read all linked numeric pages; never stop at the first title/receipt match.
  if len(visited)%10==0:print(json.dumps({'pages':len(visited),'school_records':len(records)}),flush=True)
 save('native-email-history-private.json',{'since':since,'until':until,'pages':sorted(visited),'records':records})
 results=[];used=set();unverified=[];recoverable=[];clock_samples=[]
 for delivery in deliveries:
  eid=delivery['detail'].removeprefix('email_log:');record=records.get(eid);p=R/'.runtime/notification-attempts'/f"{delivery['id']}.json";attempt=json.loads(p.read_text()) if p.exists() else None
  if not attempt:
   entry=ledger.get(delivery['id'],{});ident=identities.get(entry.get('event'))
   if ident and all(u in directory for u in entry.get('uids',[])):attempt={'subject':ident['subject'],'student_ids':[directory[u] for u in entry['uids']]}
  if not record and attempt and delivery['detail']=='final_retry_assumed_unverified':
   matches=[r for r in records.values() if r['subject']==normalize(attempt['subject']) and set(r['student_ids'])==set(attempt['student_ids'])]
   if len(matches)==1:record=matches[0];eid=record['eid']
  good=bool(record and attempt and record['subject']==normalize(attempt['subject']) and set(record['student_ids'])==set(attempt['student_ids']) and len(record['student_ids'])==delivery['recipients'])
  if good and attempt.get('started_at') and record.get('native_clock_raw') is not None:clock_samples.append((record['native_clock_raw'],attempt['started_at']))
  result={'delivery_id':delivery['id'],'eid':eid,'verified':good,'subject':record['subject'] if record else None,'recipients':delivery['recipients'],'attempt_keys':sorted(attempt) if attempt else [],'started_at':attempt.get('started_at') if attempt else None}
  result['policy_assumed_without_receipt']=delivery['detail']=='final_retry_assumed_unverified';results.append(result)
  if eid.isdigit():used.add(eid)
  if not good:unverified.append(result)
 # Actual distinct eids with same subject AND overlapping recipients are candidates,
 # not concluded duplicates until original body and provenance are compared.
 grouped=collections.defaultdict(list)
 for eid,row in records.items():
  if eid in used:grouped[row['subject']].append((eid,row))
 duplicates=[]
 for subject,rows in grouped.items():
  for i,(eid,a) in enumerate(rows):
   for fid,b in rows[i+1:]:
    overlap=set(a['student_ids'])&set(b['student_ids'])
    if overlap:duplicates.append({'subject':subject,'eids':[eid,fid],'overlap_count':len(overlap)})
 # Keep every unmatched school receipt in covered numeric range for reverse review.
 clock_offset=calibrate(clock_samples)
 coverage=clock_offset is not None and all(r.get('native_clock_raw') is not None for r in records.values())
 extras=[{'eid':eid,'subject':row['subject'],'recipients':len(row['student_ids'])} for eid,row in records.items() if clock_offset is not None and row.get('native_clock_raw') is not None and since<=row['native_clock_raw']+clock_offset<=until and eid not in used]
 save('email-reconciliation-private.json',{'results':results,'duplicates':duplicates,'unmapped_native':extras,'unverified':unverified})
 safe={'checked_at':datetime.now().astimezone().isoformat(),'since':since,'until':until,'school_clock_offset_seconds':clock_offset,'native_time_coverage_verified':coverage,'history_pages':len(visited),'expected_deliveries':len(deliveries),'verified':sum(v['verified'] for v in results),'unverified':unverified,'overlapping_subject_candidates':duplicates,'unmapped_native':extras,'new_sends':0}
 save('email-reconciliation.json',safe);print(json.dumps(safe,ensure_ascii=False),flush=True)
finally:http.close()
