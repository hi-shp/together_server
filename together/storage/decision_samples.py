"""실제 판단 입력과 출력·토큰을 보관해 로컬 모델을 같은 입력으로 비교한다."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3


def record_decision(base_dir, model, request, result, origin, context=None, usage=None):
    # SDK 설정·인증정보는 저장하지 않는다. 공지 판단 요청의 공개 스키마만 보관한다.
    from together.notices.intelligence.collection import _blob
    root = Path(base_dir) / '.runtime/intelligence/decision-samples'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    root.chmod(0o700)
    allowed = {key:request[key] for key in ('messages','response_format','temperature') if key in request}
    payload = {'schema':1,'model':model,'request':allowed,'result':result,
               'origin':origin,'context':{key:value for key,value in (context or {}).items() if key in ('source_url','department','title')},
               'usage':usage or {},'is_human_label':False}
    sha, artifact = _blob(root,payload)
    path=root/'index.sqlite3'
    with closing(sqlite3.connect(path,timeout=2)) as db, db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS samples (sha256 TEXT PRIMARY KEY,artifact TEXT,origin TEXT,first_seen TEXT,last_seen TEXT,uses INTEGER)')
        now=datetime.now().astimezone().isoformat()
        db.execute('INSERT INTO samples VALUES (?,?,?,?,?,1) ON CONFLICT(sha256) DO UPDATE SET last_seen=excluded.last_seen,uses=uses+1',(sha,artifact,origin,now,now))
    return sha


def observe_decision(base_dir,model,request,result,origin,context=None,response=None):
    try:
        usage=getattr(response,'usage',None)
        counts={key:getattr(usage,key,0) or 0 for key in ('prompt_tokens','completion_tokens','total_tokens')}
        counts['cached_input_tokens']=getattr(getattr(usage,'prompt_tokens_details',None),'cached_tokens',0) or 0
        record_decision(base_dir,model,request,result,origin,context,counts)
    except Exception as exc:
        # 저장 실패를 API 실패로 오인해 유료 호출이나 게시를 반복하지 않는다.
        from together.operations.events import event
        event('검증','판단 비교 자료 보존 실패',console=False,error=type(exc).__name__)
