"""Review the day's evidence with Work; never change live classification rules."""
from contextlib import closing
from difflib import SequenceMatcher
import hashlib
import json
import re
import sqlite3
import time

from .nightly import read, save, timestamp


def compact_notice(notice):
    result=dict(notice)
    text=result.get('body_excerpt') or result.get('body') or result.get('body_text','')
    result['body_excerpt']=text
    for name in ('body','body_text'):
        if result.get(name)==text:result.pop(name,None)
    return result


def batches(rows, maximum=96000):
    pending, size = [], 0
    for row in rows:
        length = len(json.dumps(row['request'], ensure_ascii=False).encode())
        if pending and (len(pending) == 16 or size + length > maximum):
            yield pending
            pending, size = [], 0
        pending.append(row)
        size += length
    if pending:
        yield pending


def collect(base, C):
    from together.notices.intelligence.prompts import category_request, duplicate_request
    from together.notices.intelligence.policy import CATEGORIES
    selected = {}

    def add(request, expected, source, evidence):
        key = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        row = selected.setdefault(key, {'id': key, 'request': request, 'expected': expected, 'sources': [], 'evidence': evidence})
        if source and source not in row['sources']:
            row['sources'].append(source)

    path = base / '.runtime/student/comparisons.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            for task, payload, teacher, context, prediction in db.execute('SELECT task,payload,teacher,context,prediction FROM observations WHERE at>=? AND at<=? ORDER BY at,id', (C['semantic_since'], C['until'])):
                row = json.loads(payload)
                expected = json.loads(teacher) if teacher else {}
                context = json.loads(context)
                if task == 'duplicate_decision':
                    local=json.loads(prediction)
                    if expected.get('relation')=='different_event' and local.get('label')=='different_event' and local.get('confidence',0)>=.95:
                        # Deterministic/pipeline checks still include the record;
                        # spend Work on suppressions, disagreements and actual
                        # published near-duplicates, not obvious rejected pairs.
                        continue
                    left, right = row['left'], row['right']
                    left = compact_notice(left)
                    right = compact_notice(right)
                    request = duplicate_request(left['title'], [right['title']], {'new_notice':left, 'recent_notices':[right]})
                else:
                    continue
                add(request, expected, context.get('source_url'), 'recorded_operating_input')
    path = base / '.runtime/intelligence/observations.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            notices = list(db.execute('SELECT session_id,observed_at,source_url,title,body_excerpt FROM notices ORDER BY id'))
            lookup = {(session,title):(url,body) for session,at,url,title,body in notices if timestamp(at)<=C['until']}
            final={}
            for session,at,title,category in db.execute('SELECT session_id,observed_at,title,actual_category FROM categories ORDER BY id'):
                if not C['semantic_since'] <= timestamp(at) <= C['until']:
                    continue
                final[session,title]=(at,category)
            for (session,title),(at,category) in final.items():
                source,body = lookup.get((session,title), ('',''))
                add(category_request(title,CATEGORIES,body), {'category':category}, source, 'actual_category_including_fast_rules')
    # Search for missed pairs outside the original three candidates, only in
    # this audit. It never replaces the production candidate generator.
    posts = read(base/'.runtime/intelligence/published-posts.json', {'posts':[]})['posts']
    active = [p for p in posts if not p.get('duplicate_of') and not p.get('moved_to') and p.get('board')!='장학금']
    def at(p):
        try:return timestamp(p['notice']['observed_at'])
        except (KeyError,ValueError,TypeError):return 0
    def normalized(p):return re.sub(r'\W+','',p['notice']['title'].lstrip('⭐').lower())
    fresh = [p for p in active if C['semantic_since'] <= at(p) <= C['until']]
    seen = set()
    for left in fresh:
        candidates = []
        for right in active:
            if left['article_url']==right['article_url'] or not 0 <= at(left)-at(right) <= 14*86400:
                continue
            score = SequenceMatcher(None,normalized(left),normalized(right)).ratio()
            if score >= .5:
                candidates.append((score,right))
        for _,right in sorted(candidates,key=lambda p:p[0],reverse=True)[:3]:
            pair = tuple(sorted([left['article_url'],right['article_url']]))
            if pair in seen:continue
            seen.add(pair)
            def notice(post):
                n=post['notice']
                return compact_notice(n)
            a,b=notice(left),notice(right)
            request=duplicate_request(a['title'],[b['title']],{'new_notice':a,'recent_notices':[b]})
            add(request,{'relation':'different_event'},a.get('source_url'),{'kind':'actual_separate_posts_candidate','articles':list(pair)})
    return list(selected.values())


def review(base, output, C):
    from together.integrations.work_client import WorkClient
    config = read(base/'.runtime/decision-provider.json', {})
    client = WorkClient(base,config,audit=True)
    production_cache = WorkClient(base,config)
    items = collect(base,C)
    results = read(output/'work-review-checkpoint.json', {})
    remaining = []
    for item in items:
        if item['id'] in results:continue
        cached = production_cache.cached(item['request'])
        if cached:
            results[item['id']] = {'decision':cached,'reused_operating_work_cache':True}
        else:
            remaining.append(item)
    errors=[]
    for batch in batches(remaining):
        try:
            decisions=client.decide_many([r['request'] for r in batch])
            for item,decision in zip(batch,decisions):
                results[item['id']]={'decision':decision,'reused_operating_work_cache':False}
            save(output/'work-review-checkpoint.json',results)
        except Exception as exc:
            errors.append({'error':type(exc).__name__,'pending':len(items)-len(results)})
            break
    disagreements=[]
    uncertain=[]
    for item in items:
        value=results.get(item['id'],{}).get('decision')
        if value is None:continue
        key='category' if 'category' in value else 'relation'
        row={'id':item['id'],'sources':item['sources'],'evidence':item['evidence'],'recorded':item['expected'].get(key),'reviewed':value.get(key),'reason':value.get('reason','')}
        if value.get(key) in ('판단보류','needs_context') or value.get('needs_body'):
            uncertain.append(row)
        elif row['recorded'] is not None and row['recorded']!=row['reviewed']:
            disagreements.append(row)
    proof={'expected':len(items),'checked':len(results),'pending':len(items)-len(results),'errors':errors,
           'disagreements':disagreements,'insufficient_evidence':uncertain,'finished':len(results)==len(items),
           'verified':len(results)==len(items) and not disagreements and not uncertain,
           'model_promotions':0,'posting_or_sending_requests':0,'paid_api_requests':0,
           'reference_is_work_not_human_accuracy':True,'at':time.time()}
    save(output/'work-review-checkpoint.json',results)
    save(output/'work-review.json',proof)
    return proof
