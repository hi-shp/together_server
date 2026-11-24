import os
import collections,hashlib,json,os,sqlite3,subprocess,sys,time
from pathlib import Path
from datetime import datetime
import psutil
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));F=Path('/home/ubuntu/together-final-review-20261026');O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R))
p=R/'.runtime/health/status.json';age=time.time()-p.stat().st_mtime
if age>1800:
    result=subprocess.run(['python3','tools/health_check.py','--json'],cwd=R,capture_output=True,text=True)
    (O/'fresh-health-output.json').write_text(result.stdout);(O/'fresh-health-output.json').chmod(0o600)
health=json.loads(p.read_text());(O/'health-private.json').write_text(json.dumps(health));(O/'health-private.json').chmod(0o600)
pending=[]
for p in (R/'.runtime').glob('notification-missing-*.json'):
    data=json.loads(p.read_text());pending.append({'kind':data['kind'],'requested':data.get('requested'),'missing':len(data.get('missing_hashes',[]))})
with sqlite3.connect('file:'+str(R/'.runtime/api-usage.sqlite3')+'?mode=ro',uri=True) as db:
    costs=db.execute('SELECT day,COUNT(*),SUM(cost),SUM(CASE WHEN error IS NOT NULL AND error!=\'\' THEN 1 ELSE 0 END) FROM usage GROUP BY day').fetchall()
T=F/'training-20261030';training={}
for name in ('finish-once-state.json','candidate-models/completion.json'):
    p=T/name
    if p.exists():training[name]=json.loads(p.read_text())
with sqlite3.connect('file:'+str(T/'training-budget.sqlite3')+'?mode=ro',uri=True) as db:
    cols=[x[1] for x in db.execute('PRAGMA table_info(requests)')]
    training['ledger_columns']=cols
    training['charged_and_reserved_micro_usd']=db.execute("SELECT SUM(charged),SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),COUNT(*) FROM requests").fetchone()
def size(path):
    total=0;count=0
    if not path.exists():return {'files':0,'bytes':0}
    for p in path.rglob('*'):
        try:
            if p.is_file():total+=p.stat().st_size;count+=1
        except FileNotFoundError:pass
    return {'files':count,'bytes':total}
sizes={str(p):size(p) for p in (R/'logs',R/'downloads',R/'.runtime/attachments',R/'.runtime/intelligence/edit-backups',Path('/tmp/together-1001'))}
deleted=[]
for proc in psutil.process_iter(['pid','name']):
    try:
        for file in proc.open_files():
            if file.path.endswith(' (deleted)'):deleted.append({'pid':proc.pid,'bytes':os.stat('/proc/'+str(proc.pid)+'/fd/'+str(file.fd)).st_size})
    except (psutil.NoSuchProcess,psutil.AccessDenied,FileNotFoundError,PermissionError):pass
kernel=subprocess.run(['journalctl','-k','--since','@'+str(int(json.loads((O/'context.json').read_text())['since'])),'--no-pager','-o','cat'],capture_output=True,text=True)
oom_lines=[line for line in kernel.stdout.splitlines() if 'Killed process' in line or 'oom-kill' in line]
result={'checked_at':datetime.now().astimezone().isoformat(),'health_age_seconds':round(time.time()-(R/'.runtime/health/status.json').stat().st_mtime),'health_keys':list(health),'health_critical':health.get('critical',health.get('issues')),'missing_recipient_summaries':pending,'api_days':costs,'storage':sizes,'open_deleted_count':len(deleted),'open_deleted_bytes':sum(v['bytes'] for v in deleted),'kernel_oom_records_count':len(oom_lines),'kernel_query_permission':kernel.returncode==0,'training':training}
p=O/'resources.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2));p.chmod(0o600)
print(json.dumps({k:v for k,v in result.items() if k not in ('training','storage')},ensure_ascii=False))
