"""Frozen label-blind comparisons. No API, posting, delivery, or model promotion."""
import argparse,gc,hashlib,json,os,sqlite3,time,unicodedata,re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .student.runtime import Student

def fingerprints(row):
    notes=[row] if 'left' not in row else [row['left'],row['right']]
    result=set()
    for n in notes:
        for kind,value in [('source',n.get('source_url','')),('body',n.get('body','')),('family',n.get('title',''))]:
            text=unicodedata.normalize('NFKC',value or '')
            if kind=='family':text=re.sub(r'\d+','N',re.sub(r'^\[[^\]]*\]','',text))
            text=re.sub(r'\s+','',text).lower()
            if not text or kind=='body' and len(text)<100:continue
            result.add(hashlib.sha256((kind+':'+text).encode()).hexdigest())
    return result

def connect(path):
    db=sqlite3.connect(path,timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS predictions (model TEXT, observation TEXT, at REAL, prediction TEXT, overlap INTEGER, elapsed REAL, error TEXT, PRIMARY KEY(model,observation))')
    os.chmod(path,0o600)
    return db

def run(base_dir,limit=100):
    import fcntl
    root=Path(base_dir)/'.runtime/student';manifest=json.loads((root/'shadow-models.json').read_text())
    if datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()>manifest['compare_until']:
        return {'status':'comparison_period_finished','paid_calls':0}
    with (root/'shadow.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'already_running'}
        training=(root/'training.lock').open('a')
        try:fcntl.flock(training,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            training.close()
            return {'status':'local_training_in_progress'}
        db=connect(root/'shadow-comparisons.sqlite3')
        try:
            for spec in manifest['models']:
                with sqlite3.connect('file:'+str(root/'comparisons.sqlite3')+'?mode=ro',uri=True) as source:
                    # Do not select GPT labels until all candidate predictions are committed.
                    rows=source.execute('SELECT id,task,payload FROM observations WHERE at>=? ORDER BY at',(manifest['started_at'],)).fetchall()
                rows=[r for r in rows if not db.execute('SELECT 1 FROM predictions WHERE model=? AND observation=?',(spec['id'],r[0])).fetchone()][:limit]
                if not rows:continue
                for filename,digest in spec['sha256'].items():
                    if hashlib.sha256((root/spec['directory']/filename).read_bytes()).hexdigest()!=digest:
                        raise ValueError('Frozen shadow model changed')
                model=Student(root/spec['directory'])
                known=set(json.loads((root/spec['fingerprints']).read_text()))
                exposed=set(json.loads((root/manifest['previously_exposed']).read_text()))
                try:
                    for key,task,payload in rows:
                        row=json.loads(payload);start=time.monotonic();error='';prediction={}
                        try:prediction=(model.category if task=='notice_category' else model.pair)(row)
                        except Exception as exc:error=type(exc).__name__
                        db.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?,?,?)',(spec['id'],key,time.time(),json.dumps(prediction,ensure_ascii=False),int(bool(fingerprints(row)&(known|exposed))),time.monotonic()-start,error));db.commit()
                finally:model.close();del model;gc.collect()
            result=report(root,manifest,db)
            target=root/'shadow-report.json';temp=target.with_suffix('.pending');temp.write_text(json.dumps(result,ensure_ascii=False,indent=2));temp.chmod(0o600);temp.replace(target)
            return result
        finally:db.close();training.close()

def report(root,manifest,db):
    with sqlite3.connect('file:'+str(root/'comparisons.sqlite3')+'?mode=ro',uri=True) as source:
        observations={r[0]:r[1:] for r in source.execute('SELECT id,task,teacher,context,origin FROM observations WHERE at>=?',(manifest['started_at'],))}
    out={'as_of':time.time(),'started_at':manifest['started_at'],'paid_calls':0,'gpt_agreement_not_human_accuracy':True,'frozen_models':True,'same_input_common_cohort':True,'overlap_screening':'Exact source/body and normalized title-family fingerprints; near-match source review still required','predictions_blind_to_teacher':True,'prediction_can_follow_api_receipt':True,'models':{}}
    sets={s['id']:{r[0] for r in db.execute("SELECT observation FROM predictions WHERE model=? AND error=''",(s['id'],))} for s in manifest['models']}
    common=set.intersection(*sets.values()) if sets else set()
    contaminated={r[0] for r in db.execute('SELECT observation FROM predictions WHERE overlap=1')}
    for spec in manifest['models']:
        tasks={}
        for key,pred,overlap,elapsed,error in db.execute('SELECT observation,prediction,overlap,elapsed,error FROM predictions WHERE model=?',(spec['id'],)):
            if key not in observations:continue
            task,teacher,context,origin=observations[key];m=tasks.setdefault(task,{'predictions':0,'errors':0,'common_verified':0,'agreement':0,'overlap':0,'unseen_verified':0,'unseen_agreement':0,'false_same':0,'missed_same':0,'seconds':0,'confusions':{}})
            m['predictions']+=1;m['seconds']+=elapsed
            if error:m['errors']+=1;continue
            if key not in common or not teacher or origin not in ('api','cache'):continue
            answer=json.loads(teacher);field='category' if task=='notice_category' else 'relation'
            if answer.get('_local_fallback') or answer.get('_local_primary') or field not in answer:continue
            label=json.loads(pred).get('label');truth=answer[field];m['common_verified']+=1;m['agreement']+=int(label==truth);m['overlap']+=overlap
            if key not in contaminated:m['unseen_verified']+=1;m['unseen_agreement']+=int(label==truth)
            if label!=truth:
                mistake=str(truth)+' -> '+str(label);m['confusions'][mistake]=m['confusions'].get(mistake,0)+1
            if task=='duplicate_decision':
                m['false_same']+=int(label=='same_event' and truth!='same_event');m['missed_same']+=int(label!='same_event' and truth=='same_event')
        out['models'][spec['id']]=tasks
    out['common_observations']=len(common)
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-dir',required=True);parser.add_argument('--limit',type=int,default=100);args=parser.parse_args()
    print(json.dumps(run(args.base_dir,args.limit),ensure_ascii=False))
