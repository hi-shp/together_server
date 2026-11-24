import os
"""Read every retained outbound Discord receipt and every available full attachment."""
import collections,hashlib,json,re,time
from pathlib import Path
from datetime import datetime
import requests
from dotenv import dotenv_values
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);env=dotenv_values(R/'.env')
events=[json.loads(line) for line in (O/'events.jsonl').open()]
selected={}
for e in events:
    if e.get('stage')=='Discord' and e.get('message_id'):selected[e['channel'],e['message_id']]=e
results=[];s=requests.Session();files={};errors=[];missing_local=[]
def get(url):
    for attempt in range(3):
        response=s.get(url,timeout=(5,30))
        if response.status_code!=429:return response
        time.sleep(float(response.json().get('retry_after',2))+0.2)
    return response
for index,((channel,mid),event) in enumerate(selected.items()):
    row={'channel':channel,'message_id':mid,'time':event['time']}
    try:
        with get(env[channel]+'/messages/'+mid) as res:
            row['status']=res.status_code;res.raise_for_status();data=res.json()
        content=data.get('content','')
        row['unexpected_advertising']=bool(re.search(r't\.me/|join our channel|A_TOOLSx2',content,re.I))
        row['excluded_category_sent']=channel=='WEBHOOK_URL' and '＞ 해당없음' in content
        if row['unexpected_advertising'] or row['excluded_category_sent']:errors.append(row.copy())
        row['files']=[]
        for file in data.get('attachments',[]):
            p=R/'logs'/file['filename']
            if p.parent.resolve()!=(R/'logs').resolve():raise ValueError('Unsupported attachment filename')
            files[p.name]=mid
            if not p.exists():missing_local.append(p.name);row['files'].append({'filename':p.name,'local_original_available':False});continue
            digest=hashlib.sha256()
            with s.get(file['url'],timeout=(5,30),stream=True) as stream:
                stream.raise_for_status()
                for chunk in stream.iter_content(65536):digest.update(chunk)
            match=digest.hexdigest()==hashlib.sha256(p.read_bytes()).hexdigest()
            row['files'].append({'filename':p.name,'bytes':p.stat().st_size,'sha256_matches':match})
            if not match:errors.append({'channel':channel,'message_id':mid,'error':'attachment_hash_mismatch'})
    except Exception as exc:row['error']=type(exc).__name__;errors.append({'channel':channel,'message_id':mid,'error':type(exc).__name__})
    results.append(row)
    if (index+1)%100==0:
        (O/'discord-progress.json').write_text(json.dumps({'checked':index+1,'expected':len(selected),'errors':len(errors)}));print(index+1,flush=True)
C=json.loads((O/'context.json').read_text());unmatched=[]
for p in (R/'logs').glob('log_*.txt'):
    try:at=datetime.strptime(p.stem,'log_%Y-%m-%d_%H-%M-%S').astimezone().timestamp()
    except ValueError:continue
    if C['since']<=at<=C['until'] and p.name not in files and p.name not in C.get('logs_still_in_progress_at_cut',[]):unmatched.append(p.name)
proof={'checked_at':datetime.now().astimezone().isoformat(),'expected_known_message_ids':len(selected),'checked':len(results),'message_types':dict(collections.Counter(row['channel'] for row in results)),'full_files_sha256_matches':sum(file.get('sha256_matches') is True for row in results for file in row.get('files',[])),'local_original_unavailable':missing_local,'logs_without_retained_send_receipt':sorted(unmatched),'errors':errors,'whole_channel_unknown_messages_enumerated':False,'limitation':'Webhook token can read its known message ids but cannot enumerate channel history; no bot channel-history token is available. Telemetry retention also leaves a historical gap.','new_sends':0,'results':results}
p=O/'discord.json';p.write_text(json.dumps(proof,ensure_ascii=False,indent=2));p.chmod(0o600)
print(json.dumps({k:v for k,v in proof.items() if k not in ('results','logs_without_retained_send_receipt','local_original_unavailable')},ensure_ascii=False))
