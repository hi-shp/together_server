"""Live teacher/student comparisons and date-based local-first routing.

GPT agreement is a proxy reference, never a human accuracy measurement.
"""
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,os,sqlite3,time,uuid


def settings(base_dir):
    path=Path(base_dir)/'.runtime/student/campaign.json'
    if not path.exists():return {}
    return json.loads(path.read_text(encoding='utf-8'))


def enabled(base_dir):
    return bool(settings(base_dir).get('enabled'))


@lru_cache(maxsize=1)
def model(directory):
    from .student.runtime import Student
    return Student(directory)


class Campaign:
    def __init__(self,base_dir,now=None):
        self.base=Path(base_dir);self.root=self.base/'.runtime/student';self.config=settings(base_dir)
        self.now=time.time() if now is None else now
        self.active=bool(self.config.get('enabled'))
        self.comparing=self.active and datetime.fromtimestamp(self.now,ZoneInfo('Asia/Seoul')).date().isoformat()<=self.config.get('compare_until','2026-11-30')

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        p=self.root/'comparisons.sqlite3';db=sqlite3.connect(p,timeout=10);os.chmod(p,0o600)
        db.execute('CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY,at REAL,task TEXT,version TEXT,payload TEXT,context TEXT,prediction TEXT,teacher TEXT,origin TEXT,trained_at REAL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY,at REAL,maximum REAL,state TEXT)')
        try:yield db;db.commit()
        finally:db.close()

    def begin(self,request,context):
        if not self.active:return None
        payload=json.loads(request['messages'][-1]['content']);task=request['response_format']['json_schema']['name']
        manifest=json.loads((self.root/'active.json').read_text());student=model(str(self.root/manifest['directory']))
        def notice(value):
            if isinstance(value,str):return {'title':value,'body':''}
            return dict(value,body=value.get('body') or value.get('body_excerpt',''))
        if task=='notice_category':
            row={'title':payload['title'],'body':payload.get('body','')};prediction=student.category(row)
        elif task=='duplicate_decision':
            candidates=payload.get('candidates') or [{'title':t} for t in payload.get('candidate_titles',[])]
            if len(candidates)!=1:return None  # Production compares one candidate at a time.
            row={'left':notice(payload['new_notice']),'right':notice(candidates[0])};prediction=student.pair(row)
        else:return None
        key=hashlib.sha256(json.dumps([task,payload],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        context={k:context.get(k,'') for k in ('title','source_url','department')}
        source = context.get('source_url') or row.get('left', {}).get('source_url', '')
        known_path = self.root / 'previous-source-hashes.json'
        known = set(json.loads(known_path.read_text())) if known_path.exists() else None
        source_hash = hashlib.sha256(source.encode()).hexdigest() if source else ''
        prediction['new_source_after_freeze'] = bool(source_hash and known is not None and source_hash not in known)
        prediction['source_hash'] = source_hash
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO observations (id,at,task,version,payload,context,prediction,teacher,origin) VALUES (?,?,?,?,?,?,?,?,?)',(key,self.now,task,prediction['model_version'],json.dumps(row,ensure_ascii=False),json.dumps(context,ensure_ascii=False),json.dumps(prediction,ensure_ascii=False),'','pending'))
        return {'id':key,'task':task,'row':row,'prediction':prediction}

    def finish(self,observation,result,origin):
        if not observation:return
        # Local fallback/cache results must never become teacher labels.
        valid=origin in ('api','cache') and not result.get('_local_fallback') and not result.get('_local_primary')
        with self.db() as db:
            if valid:
                db.execute("UPDATE observations SET teacher=?,origin=? WHERE id=? AND teacher=''",(json.dumps(result,ensure_ascii=False),origin,observation['id']))
            else:
                db.execute("UPDATE observations SET origin=? WHERE id=? AND teacher=''",(origin,observation['id']))

    def local_result(self,observation):
        if not observation:return None
        p=observation['prediction'];label=p['label']
        reason='로컬 학습 모델·제목과 본문의 대상·활동 내용 대조'
        if observation['task']=='notice_category':
            return {'category':label,'notice_type':'notice','needs_body':False,'reason':reason,'_local_primary':True}
        from .student.views import conflict
        row=observation['row'];guard,_=conflict(row)
        if guard:label=guard
        if label=='same_event':
            from together.notices.intelligence.focus import focus_conflict
            from together.notices.intelligence.context import department_conditions_differ
            a,b=row['left'],row['right']
            context={'new_notice':dict(a,body_excerpt=a['body']),'recent_notices':[dict(b,body_excerpt=b['body'])]}
            scoped=focus_conflict(a['title'],b['title']) or department_conditions_differ(context)
            enough=all(len(n['body'])>=160 and n.get('source_url') for n in (a,b))
            if scoped or not enough or p['confidence']<self.config.get('same_event_threshold',0.9195432946518518):
                label='needs_context'
        return {'relation':label,'matched_index':0 if label=='same_event' else -1,'reason':reason,'_local_primary':True}

    def wants_teacher(self,observation):
        if self.comparing:return True
        if not observation:return True
        # A confident score cannot bypass unresolved source/first-notice evidence.
        if observation.get('task')=='duplicate_decision' and self.local_result(observation).get('relation')=='needs_context':
            return True
        # Confidence thresholds change frequency, not a daily spend/processing cap.
        thresholds=self.config.get('review_below',{'notice_category':0.35,'duplicate_decision':0.45})
        return observation['prediction']['confidence']<thresholds.get(observation['task'],0.35)

    def reserve(self,request,model_name="gpt-5.4-mini-2026-03-17"):
        if not self.comparing:return 'not_campaign'
        from together.storage.api_usage import ApiAccounting, RATES
        rates=RATES.get(model_name)
        if not rates:return None  # Unknown prices cannot consume the protected grant.
        accounting=ApiAccounting(self.base)
        with accounting.connection() as db:
            spent=db.execute('SELECT COALESCE(SUM(cost),0) FROM usage WHERE at>=?',(self.config['balance_observed_at'],)).fetchone()[0]
            unknown=db.execute('SELECT COUNT(*) FROM usage WHERE at>=? AND cost IS NULL',(self.config['balance_observed_at'],)).fetchone()[0]
        # UTF-8 bytes conservatively bound input tokens; output is capped at 500.
        maximum=(len(json.dumps(request,ensure_ascii=False).encode())+2000)*rates[0]/1e6+500*rates[2]/1e6
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute("SELECT COALESCE(SUM(maximum),0) FROM reservations WHERE state!='settled'").fetchone()[0]
            left=self.config['grant_balance_usd']-self.config['reserve_usd']-self.config.get('prior_unsettled_usd',0)-spent-pending
            if unknown or left<maximum:return None
            key=uuid.uuid4().hex;db.execute('INSERT INTO reservations VALUES (?,?,?,?)',(key,self.now,maximum,'pending'))
        return key

    def settled(self,reservation,response):
        if reservation in (None,'not_campaign'):return
        # An unknown paid outcome retains its conservative reservation for later reconciliation.
        if getattr(response,'usage',None) is not None:
            with self.db() as db:db.execute("UPDATE reservations SET state='settled' WHERE id=?",(reservation,))
