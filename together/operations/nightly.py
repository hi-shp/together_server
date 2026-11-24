"""Weekday server audits, isolated from collection and publication decisions."""
from contextlib import closing
from datetime import datetime, time as wall_time, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
STEPS = (
    ('core', 'weekly_core_20261101.py', 'core-summary.json'),
    ('resources', 'weekly_resources_20261101.py', 'resources.json'),
    ('registry', 'collect_his_registry.py', 'discovery/official-registry-coverage.json'),
    ('discovery', 'discover_pnu_sites.py', 'discovery/progress.json'),
    ('links', 'weekly_links_20261101.py', 'links.json'),
    ('messages_index', 'resolve_native_messages_1102.py', 'message-reconciliation.json'),
    ('messages', 'resolve_direct_full_1102.py', 'message-final-reconciliation.json'),
    ('email', 'resolve_native_emails_1102.py', 'email-reconciliation.json'),
    ('publications', 'weekly_publications_20261101.py', 'publications.json'),
    ('discord', 'weekly_discord_20261101.py', 'discord.json'),
    ('pipeline', 'pipeline_reconcile.py', 'pipeline-reconciliation.json'),
)


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.chmod(0o600)
    temporary.replace(path)


def scheduled_slot(now, manual=False):
    now = now.astimezone(KST)
    if now.weekday() >= 5:
        return None
    if manual:
        return now
    slot = datetime.combine(now.date(), wall_time(22), KST)
    if slot > now:
        slot -= timedelta(days=1)
    while slot.weekday() >= 5:
        slot -= timedelta(days=1)
    return slot


def timestamp(value):
    if isinstance(value, (int, float)):
        return value
    parsed = datetime.fromisoformat(value)
    return (parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed).timestamp()


def activity(base, slot):
    """First-seen sources, not repeated observations or only successful posts."""
    start = datetime.combine(slot.date(), wall_time.min, KST).timestamp()
    first = {}
    found = False
    for name,table,column in [('.runtime/intelligence/observations.sqlite3','notices','observed_at'),
                              ('.runtime/intelligence/collection/index.sqlite3','samples','first_seen')]:
        path = base / name
        if not path.exists():continue
        found = True
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for url,at in db.execute(f'SELECT source_url,MIN({column}) FROM {table} GROUP BY source_url'):
                first[url] = min(first.get(url,float('inf')),timestamp(at))
    pending = read(base/'.runtime/pending-notices.json',{})
    for row in pending.get('pending',[]) + pending.get('completed',[]):
        if row.get('source_url') and row.get('created_at'):
            url=row['source_url'];first[url]=min(first.get(url,float('inf')),timestamp(row['created_at']))
    return sum(start <= at <= slot.timestamp() for at in first.values()) if found else None


def verdict(step, proof):
    if step == 'core':
        return proof.get('telemetry', {}).get('invalid_lines') == 0 and proof.get('telemetry', {}).get('retained_start_covers_requested') is True
    if step == 'resources':
        return not proof.get('health_critical')
    if step == 'registry':
        return len(proof) == 2 and all(p.get('complete') and p['covered'] == p['expected'] for p in proof)
    if step == 'discovery':
        return proof.get('sites_visited') == proof.get('sites') and all(k == 'ok' or not v for k,v in proof.get('site_states', {}).items())
    if step == 'links':
        good = {'verified', 'verified_authenticated', 'school_disabled'}
        return proof.get('checked') == proof.get('physical_boards') and all(r['result'] in good for r in proof['results'])
    if step == 'messages':
        return proof.get('verified_events') == proof.get('expected_events') and not any(proof.get(k) for k in ('unverified', 'duplicates', 'native_not_in_intents'))
    if step == 'email':
        return proof.get('verified') == proof.get('expected_deliveries') and proof.get('native_time_coverage_verified') is True and not any(proof.get(k) for k in ('unverified', 'overlapping_subject_candidates', 'unmapped_native'))
    if step == 'publications':
        return proof.get('checked') == proof.get('expected_receipts') and not proof.get('errors') and all(not r.get('error') and not r.get('stars_before_current_day') for r in proof.get('star_search', []))
    if step == 'discord':
        # Unknown channel history and older ID gaps remain explicitly separate.
        return proof.get('checked') == proof.get('expected_known_message_ids') and not any(proof.get(k) for k in ('errors', 'local_original_unavailable', 'logs_without_retained_send_receipt'))
    if step == 'pipeline':
        return not any(proof.get(k) for k in ('new_decision_without_receipt_binding_or_pending', 'source_bound_to_multiple_posts', 'same_exact_title_review_candidates'))
    return True


def advance(state, step, until, verified):
    if verified:
        state.setdefault('cursors', {})[step] = until


def context(base, state, slot):
    default = slot.timestamp() - 7 * 86400
    cursors = state.get('cursors', {})
    since = min([cursors.get(k, default) for k in ('core', 'messages', 'email', 'publications', 'discord', 'pipeline', 'semantic')])
    import psutil
    running = set()
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if process.cwd() == str(base) and any(Path(v).name in ('main.py','run_main.py') for v in process.info['cmdline'] or []):
                running.update(Path(v.path).name for v in process.open_files() if Path(v.path).parent == base/'logs')
        except (psutil.AccessDenied,psutil.NoSuchProcess):
            pass
    return {'scheduled_kst': slot.isoformat(), 'since': since, 'until': slot.timestamp(),
            'message_since': cursors.get('messages', default), 'email_since': cursors.get('email', default),
            'publication_since': cursors.get('publications', default), 'semantic_since': cursors.get('semantic', default),
            'discord_since':cursors.get('discord',default),'pipeline_since':cursors.get('pipeline',default),
            'logs_still_in_progress_at_cut': sorted(running), 'prior_gaps_preserved': True}


def run(base, *, now=None, manual=False, plan=False):
    import fcntl
    base = Path(base).resolve()
    now = now or datetime.now(KST)
    slot = scheduled_slot(now, manual)
    root = base / '.runtime/nightly-audit'
    root.mkdir(exist_ok=True, mode=0o700)
    lock = (root / 'execution.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        if slot is not None and not manual and not plan:
            save(root/'deferred'/slot.strftime('%Y%m%dT%H%M%S.json'),{'scheduled_kst':slot.isoformat(),'status':'queued_after_active_audit'})
        return {'status': 'already_running'}
    try:
        if slot is None:
            return {'status': 'skipped_weekend', 'work_calls': 0}
        key = ('manual-' if manual else '') + slot.strftime('%Y%m%dT%H%M%S')
        output = root / 'runs' / key
        status = read(output / 'status.json', {})
        if status.get('status') in ('complete', 'completed_with_evidence_gaps', 'skipped_no_new_notices'):
            return {'status': 'already_finished', 'run': key}
        count = activity(base, slot)
        result = {'status': 'planned', 'run': key, 'slot': slot.isoformat(), 'new_sources': count,
                  'weekdays_only': True, 'work_only_no_api_fallback': True}
        if plan:
            return result
        if count == 0:
            result.update(status='skipped_no_new_notices', work_calls=0)
            save(output / 'status.json', result)
            return result
        head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=base,text=True).strip()
        key += '-'+head[:12]
        output = root/'runs'/key
        status = read(output/'status.json',{})
        if status.get('status') in ('complete','completed_with_evidence_gaps'):
            return {'status':'already_finished','run':key}
        result['run']=key
        result['revision']=head
        state_path = root / 'state.json'
        state = read(state_path, {'cursors': {}})
        C = read(output / 'context.json') or context(base, state, slot)
        save(output / 'context.json', C)
        save(output / 'unverified-intents-private.json', [])
        result.update(status='running', started_at=time.time(), steps=status.get('steps', {}))
        save(output / 'status.json', result)
        env = os.environ.copy()
        env.update(TOGETHER_ROOT=str(base), TOGETHER_AUDIT_OUTPUT=str(output),
                   OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', API_USAGE_CONTEXT='nightly-audit')
        (output / 'discovery').mkdir(exist_ok=True)
        env.update(PNU_DISCOVERY_DIR=str(output/'discovery'),PNU_EXISTING_PAGES=str(base/'pages.csv'))
        helpers = base / 'tools/nightly_checks'
        for step, script, proof_file in STEPS:
            previous = result['steps'].get(step, {})
            if previous.get('finished'):
                continue
            if subprocess.check_output(['git','rev-parse','HEAD'],cwd=base,text=True).strip()!=head:
                result.update(status='interrupted',reason='source_revision_changed')
                save(output/'status.json',result)
                save(root/'latest.json',{'run':key,'status':'interrupted','path':str(output),'at':time.time()})
                return result
            result['current_step'] = step
            save(output / 'status.json', result)
            with (output / (step + '.private.log')).open('a') as log:
                process = subprocess.run([sys.executable, str(helpers / script)], cwd=base, env=env, stdout=log, stderr=subprocess.STDOUT)
            proof = read(output / proof_file, {})
            ok = process.returncode == 0 and bool(proof)
            stable = subprocess.check_output(['git','rev-parse','HEAD'],cwd=base,text=True).strip()==head
            verified = ok and stable and verdict(step, proof)
            result['steps'][step] = {'finished': ok, 'verified': verified, 'exit_code': process.returncode, 'proof': proof_file}
            advance(state, step, C['until'], verified)
            save(state_path, state)
            save(output / 'status.json', result)
            if not ok or not stable:
                # Keep this run resumable, rather than redoing completed reads.
                result['status'] = 'interrupted'
                save(output / 'status.json', result)
                save(root/'latest.json',{'run':key,'status':'interrupted','path':str(output),'at':time.time()})
                return result
        from together.operations.nightly_semantic import review
        semantic = review(base, output, C)
        if subprocess.check_output(['git','rev-parse','HEAD'],cwd=base,text=True).strip()!=head:
            semantic['verified']=False
        result['steps']['semantic'] = {'finished': semantic['finished'], 'verified': semantic['verified'], 'proof': 'work-review.json'}
        advance(state, 'semantic', C['until'], semantic['verified'])
        save(state_path, state)
        from together.operations.nightly_report import finish
        finish(base, output, C, result)
        result['status'] = 'complete' if all(v['verified'] for v in result['steps'].values()) else 'completed_with_evidence_gaps'
        result['finished_at'] = time.time()
        save(output / 'status.json', result)
        save(root / 'latest.json', {'run': key, 'status': result['status'], 'path': str(output), 'at': result['finished_at']})
        return {k: result[k] for k in ('run', 'status', 'slot', 'new_sources')}
    finally:
        lock.close()
