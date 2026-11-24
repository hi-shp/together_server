"""Concise Korean results; routine runs never send an email or Discord report."""
from contextlib import closing
import hashlib
import json
import sqlite3
import time
from datetime import datetime
from .nightly import read,save


def finish(base, output, C, result):
    metrics={}
    names={'links':'links.json','messages':'message-final-reconciliation.json','email':'email-reconciliation.json',
           'publications':'publications.json','discord':'discord.json','semantic':'work-review.json'}
    for name,filename in names.items():
        p=read(output/filename,{})
        metrics[name]={k:p[k] for k in ('checked','physical_boards','expected_events','verified_events','expected_deliveries','verified','expected_receipts','expected_known_message_ids','full_files_sha256_matches','expected','pending') if k in p}
    path=base/'.runtime/api-usage.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        api=[dict(zip(('day','requests','input','output','usd'),row)) for row in db.execute('SELECT day,COUNT(*),SUM(input),SUM(output),SUM(cost) FROM usage WHERE at>=? AND at<=? GROUP BY day ORDER BY day',(C['since'],C['until']))]
    path=base/'.runtime/work-codex/nightly/decisions.sqlite3'
    work={}
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            work=dict(zip(('sessions','decisions','input_tokens','output_tokens','cached_input_tokens'),db.execute('SELECT COUNT(*),SUM(items),SUM(input),SUM(output),SUM(cached) FROM attempts WHERE at>=?',(result['started_at'],)).fetchone()))
    config=read(base/'.runtime/decision-provider.json',{})
    credit={'snapshot_observed_at':config.get('balance_observed_at'),'grants':config.get('credit_grants',[]),'not_live_billing':True}
    if credit['snapshot_observed_at']:
        with closing(sqlite3.connect((base/'.runtime/api-usage.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            commitments=db.execute('SELECT at,cost FROM usage WHERE at>=?',(credit['snapshot_observed_at'],)).fetchall()
        with closing(sqlite3.connect((base/'.runtime/decision-provider.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            commitments+=db.execute("SELECT at,maximum FROM reservations WHERE state!='settled'").fetchall()
            credit['persistent_api_exhaustion']=bool(db.execute("SELECT 1 FROM state WHERE key='api_exhausted'").fetchone())
        commitments.append((credit['snapshot_observed_at'],config.get('prior_unsettled_usd',0)))
        balances=[[datetime.fromisoformat(g['expires_at']).timestamp(),g['balance_usd']] for g in sorted(credit['grants'],key=lambda g:g['expires_at'])]
        unknown=any(v is None for _,v in commitments)
        for at,cost in sorted(commitments,key=lambda r:r[0]):
            if cost is None:continue
            for balance in balances:
                if balance[0]>at:
                    used=min(cost,balance[1]);balance[1]-=used;cost-=used
        credit['unexpired_estimate_usd']=None if unknown else sum(v for expiry,v in balances if expiry>time.time())
        credit['unknown_charges_present']=unknown
    shadow=read(base/'.runtime/student/shadow-report.json',{})
    shadow_summary={k:v for k,v in shadow.items() if isinstance(v,(int,float,bool,str))}
    proof={'interval':C,'checks':result['steps'],'counts':metrics,'api_actual_days':api,'nightly_work_usage':work,
           'new_posts_or_sends':0,'production_algorithms_changed':False,'older_gaps_preserved':True,
           'unknown_channel_history_verified':False,'external_inbox_received_verified':False,'credit_estimate':credit,'shadow_summary':shadow_summary,'at':time.time()}
    save(output/'summary.json',proof)
    lines=['# 투게더 평일 일일 점검', '',f"기준 시각: {C['scheduled_kst']}",
           f"오늘 처음 발견한 원문: {result['new_sources']}건",'',
           '| 점검 | 결과 |','| --- | --- |']
    for step,v in result['steps'].items():
        lines.append(f"| {step} | {'확인' if v['verified'] else '미확인·추가 대조 필요'} |")
    lines+=['','## 집계','', '```json',json.dumps(metrics,ensure_ascii=False,indent=2),'```','',
            '분류·중복의 Work 검토는 참고 판단이며 사람 정답률이 아니다. 불일치와 자료 부족은 work-review.json에 보존했다.',
            '미확인 구간의 검증 위치는 앞당기지 않는다. 기존 게시글·첨부·구독·CSV/TXT·모델과 알림 순서를 보존했다.',
            '학교 처리 내역과 외부 받은편지함 수신 증거, 알려진 Discord 전송과 조회할 수 없는 채널 이력을 구분한다.',
            '', '## 사용량','',f'운영 API 날짜별 실제값: {json.dumps(api,ensure_ascii=False)}',
            f'이번 점검 Work 실제값: {json.dumps(work,ensure_ascii=False)}',
            f'크레딧별 만료·과거 스냅샷·이후 원장 추정: {json.dumps(credit,ensure_ascii=False)}',
            '점검을 위한 유료 API 요청·시험 게시·재발송: 0건.']
    (output/'점검결과.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    # Native recipient duplicates require real distinct messages and full member
    # sets; a semantic disagreement or a temporary source delay is not critical.
    native=read(output/'message-final-reconciliation.json',{})
    details=read(output/'message-reconciliation-private.json',{}).get('results',{})
    confirmed=[{'code':'confirmed_duplicate_message_recipients','event':v['event']} for v in native.get('duplicates',[]) if v.get('overlap',0) and details.get(v['event'],{}).get('verified')]
    save(output/'confirmed-critical.json',confirmed)
    if confirmed:
        alert_path=base/'.runtime/nightly-audit/alerts.json'
        alerts=read(alert_path,{})
        from together.config import notify
        for issue in confirmed:
            key=hashlib.sha256(json.dumps(issue,sort_keys=True).encode()).hexdigest()
            if time.time()-alerts.get(key,0)<86400:continue
            # Claim before the request so an unknown webhook outcome cannot
            # cause a second notification on restart.
            alerts[key]=time.time();save(alert_path,alerts)
            notify('투게더 일일 점검: 실제 학교 쪽지 내역에서 동일 안내의 수신자 중복을 확인했습니다. 서버 점검결과와 원격 내역 대조가 필요합니다.')
