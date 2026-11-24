import os
"""Read-only complete retained-record pass for the delayed Nov 1 scheduled run."""
import collections,csv,hashlib,json,os,re,sqlite3,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timedelta
import psutil
from dotenv import dotenv_values
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source')); F=Path('/home/ubuntu/together-final-review-20261026'); O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);O.mkdir(parents=True,exist_ok=True,mode=0o700)
sys.path.insert(0,str(R))
from together.operations.events import safe_text
def save(name,value):
    p=O/name;t=p.with_suffix('.pending');t.write_text(json.dumps(value,ensure_ascii=False,indent=2));t.chmod(0o600);t.replace(p)
context_path=O/'context.json'
assert context_path.exists(), 'Run through tools/nightly_audit.py'
if context_path.exists():C=json.loads(context_path.read_text())
else:
    end=time.time();C={'scheduled_kst':'2026-11-10T21:00:00+09:00','started_at':datetime.now().astimezone().isoformat(),'since':end-7*86400,'until':end,'delayed_run':True};save('context.json',C)
events=[];files=[];bad=0;scanned=0;first=None;last=None;period_count=0
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()
warnings=collections.Counter();recent_diagnostics=collections.Counter()
for p in sorted((R/'.runtime/telemetry').glob('events.jsonl*')):
    files.append(p.name)
    for line in p.open():
        scanned+=1
        try:e=json.loads(line);at=datetime.fromisoformat(e['time']).timestamp()
        except (ValueError,KeyError):bad+=1;continue
        if C['since']<=at<=C['until']:
            period_count+=1;first=min(first or at,at);last=max(last or at,at)
            if e.get('level') in ('ERROR','WARN'):
                warnings[(e.get('level'),e.get('stage'),e.get('message'))]+=1
                if e.get('revision')==head:recent_diagnostics[(e.get('stage') or '')+':'+(e.get('error') or e.get('message',''))]+=1
            if e.get('stage') in ('Discord','중복','분류','게시','쪽지','이메일','보강','첨부','정리'):events.append(e)
from together.operations.audit_reader import retained_audit_events,merge_retained_events
events=merge_retained_events(events,retained_audit_events(R,C['since'],C['until']))
events.sort(key=lambda e:e['time'])
with (O/'events.jsonl').open('w') as h:
    for e in events:h.write(json.dumps(e,ensure_ascii=False)+'\n')
(O/'events.jsonl').chmod(0o600)
warning_rows=[{'level':k[0],'stage':k[1],'message':safe_text(k[2]),'count':v} for k,v in warnings.items()]
save('diagnostics.json',warning_rows)
decisions=[e for e in events if e.get('stage') in ('중복','분류')]
decision_issues=[]
for e in decisions:
    if e.get('stage')=='중복' and e.get('similarity') is not None and not 0<=e['similarity']<=1:decision_issues.append({'notice_id':e.get('notice_id'),'issue':'similarity_range'})
    if e.get('stage')=='분류' and not e.get('category'):decision_issues.append({'notice_id':e.get('notice_id'),'issue':'category_missing'})
save('decision-structure.json',{'total':len(decisions),'by_stage':dict(collections.Counter(e['stage'] for e in decisions)),'issues':decision_issues,'semantic_accuracy_verified':False})
states={};schemas={}
for name in ('email-outbox.sqlite3','notification-deliveries.sqlite3','intelligence/observations.sqlite3','api-usage.sqlite3'):
    with sqlite3.connect('file:'+str(R/'.runtime'/name)+'?mode=ro',uri=True) as db:
        schemas[name]={table:[row[1] for row in db.execute('PRAGMA table_info('+table+')')] for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if name=='notification-deliveries.sqlite3':
            for table in ('deliveries','delivery_recipients'):states[table]=dict(db.execute('SELECT state,COUNT(*) FROM '+table+' GROUP BY state'))
        if name=='email-outbox.sqlite3':states['jobs']=dict(db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))
save('schemas.json',schemas)
receipts=json.loads((R/'.runtime/published-receipts.json').read_text());save('receipts.json',receipts)
star=[]
for p in (R/'.runtime/star-cleanup').glob('*.json'):
    if p.stem.isdigit():
        v=json.loads(p.read_text());star.append({'id':p.stem,'verified':bool(v.get('verified')),'reconciled_without_resubmit':v.get('reconciled_without_resubmit')})
save('star-proofs.json',star)
runtime={}
for name in ('runner.json','status.json'):
    p=R/'.runtime/health'/name
    if p.exists():runtime[name]=json.loads(p.read_text())
save('health-private.json',runtime)
caps=[];procs=[]
for p in psutil.process_iter(['pid','name','cmdline']):
    try:
        if 'python' in (p.info['name'] or '').lower() and p.cwd()==str(R):
            names={Path(a).name for a in p.info['cmdline'] or []}
            if names & {'run_main.py','main.py'}:
                procs.append({'pid':p.pid,'role':'main' if 'main.py' in names else 'runner','daily_budget':p.environ().get('GPT_DAILY_BUDGET_USD')})
    except (psutil.NoSuchProcess,psutil.AccessDenied):pass
env=dotenv_values(R/'.env');disk=psutil.disk_usage(R);vfs=os.statvfs(R)
pages=list(csv.DictReader((R/'pages.csv').open(encoding='utf-8-sig')))
for name in ('final-proof-1101.json','budget-finalization-proof.json','verification-approved.json'):
    p=F/name
    if p.exists():save(name,json.loads(p.read_text()))
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()
recent=[e for e in events if e.get('revision')==head and e.get('level') in ('ERROR','WARN')]
summary={'context':C,'revision':head,'telemetry':{'files':len(files),'lines_scanned':scanned,'events_in_period':period_count,'decision_and_delivery_events_retained':len(events),'invalid_lines':bad,'first_retained_at':first,'last_retained_at':last,'retained_start_covers_requested':first is not None and first<=C['since']+3600},'diagnostic_groups':len(warnings),'current_revision_diagnostics':dict(recent_diagnostics),'ledger_states':states,'receipts':dict(collections.Counter(v.get('state') for v in receipts.values())),'star_proofs':{'total':len(star),'unverified':[v['id'] for v in star if not v['verified']]},'pending_star_boards':len(json.loads((R/'.runtime/star-cleanup/pending.json').read_text())['items']),'csv_rows':len(pages),'processes':procs,'runner':runtime.get('runner.json',{}),'health_issues':runtime.get('status.json',{}).get('issues'), 'configured_daily_budget':env.get('GPT_DAILY_BUDGET_USD'),'email_concurrency':env.get('PLATO_EMAIL_CONCURRENCY','1'),'disk_free_gib':round(disk.free/1024**3,2),'inode_free_percent':round(vfs.f_favail/vfs.f_files*100,2),'memory_available_mib':round(psutil.virtual_memory().available/1024**2,1),'night_mode':json.loads((R/'.runtime/verification-mode.json').read_text())}
save('core-summary.json',summary)
print(json.dumps(summary,ensure_ascii=False))
