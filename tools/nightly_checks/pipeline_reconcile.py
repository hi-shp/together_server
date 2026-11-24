import os
import json,collections,sys,time
from pathlib import Path
from datetime import datetime
R=Path(os.environ.get('TOGETHER_ROOT','/home/ubuntu/together/Source'));O=Path(os.environ['TOGETHER_AUDIT_OUTPUT']);sys.path.insert(0,str(R))
from together.notices.intelligence.representative import _subject
C=json.loads((O/'context.json').read_text());receipts=json.loads((R/'.runtime/published-receipts.json').read_text());posts=json.loads((R/'.runtime/intelligence/published-posts.json').read_text())['posts'];pending=json.loads((R/'.runtime/pending-notices.json').read_text());by_source=collections.defaultdict(list);groups=collections.defaultdict(list)
for p in posts:
 if p.get('duplicate_of') or p.get('moved_to'):continue
 for u in p.get('sources',[]):by_source[u].append(p['article_url'])
 groups[_subject(p['notice']['title'])].append(p)
decisions=collections.defaultdict(list)
for l in (O/'events.jsonl').open():
 e=json.loads(l)
 if e.get('stage')=='중복' and e.get('source_url'):decisions[e['source_url']].append(e)
index={r.get('source_url'):r for r in receipts.values()};unfinished={x.get('url') or x.get('source_url') for x in pending['pending']};missing=[]
for source,events in decisions.items():
 last=events[-1]
 if last.get('result')!='중복' and source not in index and source not in by_source and source not in unfinished:
  missing.append({'source':source,'title':last.get('title'),'result':last.get('result'),'events':len(events)})
risky=[]
for title,rs in groups.items():
 if len(rs)<2 or len(title)<10 or not any(C['publication_since']<=datetime.fromisoformat(x['notice']['observed_at']).timestamp()<=C['until'] for x in rs):continue
 if all(x['board']=='장학금' for x in rs):continue
 risky.append({'title':title,'posts':[{'url':x['article_url'],'source':x['notice']['source_url'],'department':x['notice'].get('department'),'at':x['notice']['observed_at'],'board':x['board']} for x in rs]})
result={'at':time.time(),'source_decisions':len(decisions),'new_decision_without_receipt_binding_or_pending':missing,'source_bound_to_multiple_posts':{u:ls for u,ls in by_source.items() if len(set(ls))>1},'same_exact_title_review_candidates':risky,'pending_count_now':len(pending['pending']),'pending_records_fields':[sorted(x) for x in pending['pending']],'first_notice_gap_not_automatically_proven':False};(O/'pipeline-reconciliation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
