"""Summarize live comparisons without claiming human accuracy."""
import json,time,sqlite3
from pathlib import Path
from collections import Counter
from .campaign import Campaign

def report(base_dir):
    campaign=Campaign(base_dir);tasks={}
    with campaign.db() as db:
        for task in ['notice_category','duplicate_decision']:
            rows=db.execute('SELECT prediction,teacher,origin FROM observations WHERE task=?',(task,)).fetchall()
            seen=confirmed=equal=fresh=fresh_equal=false_same=missed_same=0
            for p,t,origin in rows:
                seen+=1
                if not t:continue
                p,t=json.loads(p),json.loads(t);field='category' if task=='notice_category' else 'relation'
                confirmed+=1;match=p['label']==t.get(field);equal+=match
                if p.get('new_source_after_freeze'):fresh+=1;fresh_equal+=match
                if task=='duplicate_decision':
                    false_same+=p['label']=='same_event' and t.get(field)!='same_event'
                    missed_same+=p['label']!='same_event' and t.get(field)=='same_event'
            tasks[task]={'observations':seen,'teacher_verified':confirmed,'agreement':equal,'unconfirmed':seen-confirmed,'new_sources':fresh,'new_source_agreement':fresh_equal,'raw_false_duplicate_candidates':false_same,'raw_missed_duplicates':missed_same}
    p=Path(base_dir)/'.runtime/api-usage.sqlite3';daily=[]
    if p.exists():
        with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as db:
            daily=[dict(zip(['day','requests','input_tokens','output_tokens','cached_tokens','estimated_cost_usd'],r)) for r in db.execute("SELECT day,COUNT(*),SUM(input),SUM(output),SUM(cached),SUM(cost) FROM usage WHERE at>=? GROUP BY day ORDER BY day",(campaign.config['balance_observed_at'],))]
    result={'as_of':time.time(),'gpt_agreement_is_not_human_accuracy':True,'tasks':tasks,'daily':daily,'credit_snapshot':{k:campaign.config.get(k) for k in ['grant_balance_usd','reserve_usd','expires','balance_observed_at','other_grant_balance_usd','other_grant_expires']},'annual_target_usd':5,'annual_goal_verified':False}
    result['estimated_grant_balance_usd']=campaign.config['grant_balance_usd']-sum(r['estimated_cost_usd'] or 0 for r in daily)
    with campaign.db() as db:
        pending=db.execute("SELECT COALESCE(SUM(maximum),0) FROM reservations WHERE state!='settled'").fetchone()[0]
        result['model_versions']=[dict(zip(['task','version','observations','trained'],r)) for r in db.execute('SELECT task,version,COUNT(*),SUM(trained_at>0) FROM observations GROUP BY task,version')]
    result['unsettled_upper_estimate_usd']=pending+campaign.config.get('prior_unsettled_usd',0)
    result['additional_spend_available_estimate_usd']=max(0,result['estimated_grant_balance_usd']-campaign.config['reserve_usd']-result['unsettled_upper_estimate_usd'])
    result['balance_is_estimate_not_live_billing']=True
    (campaign.root/'latest-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--base-dir',required=True);args=parser.parse_args()
    print(json.dumps(report(args.base_dir),ensure_ascii=False))
