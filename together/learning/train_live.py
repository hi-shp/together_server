"""Free, local correction learning from already received GPT decisions.

Predictions are recorded before labels; calibration is only a regression gate.
"""
import argparse,fcntl,hashlib,json,os,shutil,time
from pathlib import Path
import numpy as np
from together.learning.campaign import Campaign
from together.learning.student.runtime import Student,category_text
from together.learning.student.views import pairtext


def run(base_dir,force=False):
    campaign=Campaign(base_dir);root=campaign.root
    if not campaign.active:return {'status':'disabled'}
    root.mkdir(exist_ok=True,parents=True)
    with (root/'training.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'already_running'}
        now=time.time();statepath=root/'training-state.json'
        if statepath.exists() and not force and now-json.loads(statepath.read_text()).get('at',0)<23*3600:
            return {'status':'daily_local_training_already_checked'}
        with campaign.db() as db:
            rows=db.execute("SELECT id,task,payload,teacher,prediction FROM observations WHERE teacher!='' AND trained_at=0 ORDER BY at").fetchall()
        learned=[]
        for key,task,payload,teacher,prediction in rows:
            t=json.loads(teacher);field='category' if task=='notice_category' else 'relation';label=t.get(field)
            if label in ('판단보류','needs_context') or t.get('needs_body') or t.get('notice_type')=='uncertain':continue
            learned.append((key,task,json.loads(payload),label,json.loads(prediction)))
        if len(learned)<3:return {'status':'waiting_for_new_teacher_evidence','available':len(learned),'paid_calls':0}
        active=json.loads((root/'active.json').read_text());student=Student(root/active['directory'])
        calibration=json.loads((root/'calibration.json').read_text())
        def score(task):
            chosen=[r for r in calibration if r['task']==task]
            method=student.category if task=='notice_category' else student.pair
            return sum(method(r['row'])['label']==r['label'] for r in chosen)
        before={task:score(task) for task in ('notice_category','duplicate_decision')}
        originals={}
        changes=0
        for _ in range(4):
            for key,task,row,label,prediction in learned:
                name='category' if task=='notice_category' else next((n for n in student.meta['pair_selection'][:2] if n.startswith('difference')),None)
                if name is None:continue
                classes=student.meta['category_classes' if task=='notice_category' else 'pair_classes']
                if label not in classes:continue
                text=category_text(row) if name=='category' else pairtext(row)
                indices,values=student.vector(name,text)
                if not len(indices):continue
                coef=student.array(name+'_coef');bias=student.array(name+'_bias')
                for suffix,array in [('coef',coef),('bias',bias)]:
                    if name+'_'+suffix not in originals:originals[name+'_'+suffix]=array.copy()
                scores=coef[:,indices]@values+bias;expected=classes.index(label)
                competitors=scores.copy();competitors[expected]=-np.inf;wrong=int(competitors.argmax())
                loss=1+scores[wrong]-scores[expected]
                if loss<=0:continue
                step=min(.05,float(loss)/(2*float(values@values)+2))
                coef[expected,indices]+=step*values;coef[wrong,indices]-=step*values
                bias[expected]+=step;bias[wrong]-=step;changes+=1
        after={task:score(task) for task in before}
        accepted=changes>0 and all(after[k]>=before[k] for k in before)
        result={'at':now,'status':'accepted_for_next_comparisons' if accepted else 'kept_existing_model','samples':len(learned),'disagreements':sum(p['label']!=y for _,_,_,y,p in learned),'before':before,'after':after,'calibration_only_not_new_accuracy':True,'paid_calls':0}
        if accepted:
            version='live-'+str(int(now));directory=root/version;directory.mkdir(mode=0o700)
            arrays={key:student.cache[key] if key in student.cache else student.weights[key] for key in student.weights.files}
            np.savez_compressed(directory/'weights.npz',**arrays)
            meta=dict(student.meta,version=version);(directory/'student.json').write_text(json.dumps(meta,ensure_ascii=False))
            manifest={'directory':version,'at':now,'trained_observations':[key for key,*_ in learned]}
            tmp=root/'active.pending';tmp.write_text(json.dumps(manifest));tmp.replace(root/'active.json')
            result['version']=version
        with campaign.db() as db:
            db.executemany('UPDATE observations SET trained_at=? WHERE id=?',[(now,key) for key,*_ in learned])
        statepath.write_text(json.dumps(result,ensure_ascii=False,indent=2));student.close()
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-dir',required=True);parser.add_argument('--force',action='store_true');args=parser.parse_args()
    print(json.dumps(run(Path(args.base_dir),args.force),ensure_ascii=False))
