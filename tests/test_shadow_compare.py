import json,sqlite3,tempfile,unittest
from pathlib import Path
from together.learning.shadow_compare import connect,report,fingerprints

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.db=connect(self.root/'shadow.sqlite3');self.addCleanup(self.db.close)
        with sqlite3.connect(self.root/'comparisons.sqlite3') as db:
            db.execute('CREATE TABLE observations(id TEXT,task TEXT,teacher TEXT,context TEXT,origin TEXT,at REAL)')
        self.manifest={'started_at':10,'models':[{'id':'a'},{'id':'b'}]}
    def observation(self,key,task,teacher,origin='api'):
        with sqlite3.connect(self.root/'comparisons.sqlite3') as db:db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?)',(key,task,json.dumps(teacher),'{}',origin,11))
    def prediction(self,model,key,label,overlap=0,error=''):
        self.db.execute('INSERT INTO predictions VALUES(?,?,?,?,?,?,?)',(model,key,12,json.dumps({'label':label}),overlap,.1,error));self.db.commit()
    def test_partial_candidates_not_counted_as_fair_comparison(self):
        self.observation('one','notice_category',{'category':'해당없음'});self.prediction('a','one','해당없음')
        r=report(self.root,self.manifest,self.db);self.assertEqual(r['common_observations'],0);self.assertEqual(r['models']['a']['notice_category']['common_verified'],0)
    def test_fallback_cannot_become_reference(self):
        self.observation('one','notice_category',{'category':'해당없음','_local_fallback':True},'cache')
        for name in ('a','b'):self.prediction(name,'one','해당없음')
        self.assertEqual(report(self.root,self.manifest,self.db)['models']['a']['notice_category']['common_verified'],0)
    def test_any_model_exposure_excludes_case_from_common_unseen(self):
        self.observation('one','notice_category',{'category':'해당없음'})
        self.prediction('a','one','해당없음',1);self.prediction('b','one','해당없음')
        for stats in report(self.root,self.manifest,self.db)['models'].values():self.assertEqual(stats['notice_category']['unseen_verified'],0)
    def test_false_merges_and_missed_duplicates_separate(self):
        for key,truth,pred in [('one','different_event','same_event'),('two','same_event','different_event')]:
            self.observation(key,'duplicate_decision',{'relation':truth})
            for name in ('a','b'):self.prediction(name,key,pred)
        stats=report(self.root,self.manifest,self.db)['models']['a']['duplicate_decision'];self.assertEqual((stats['false_same'],stats['missed_same']),(1,1))
    def test_failed_prediction_is_visible_and_not_common(self):
        self.observation('one','notice_category',{'category':'해당없음'});self.prediction('a','one','',error='MemoryError');self.prediction('b','one','해당없음')
        stats=report(self.root,self.manifest,self.db);self.assertEqual(stats['common_observations'],0);self.assertEqual(stats['models']['a']['notice_category']['errors'],1)
    def test_fingerprints_preserve_source_and_share_repeated_event_family(self):
        a=fingerprints({'title':'2026년 2학기 장학','body':'본문'*60,'source_url':'https://a/1'})
        b=fingerprints({'title':'2027년 1학기 장학','body':'본문'*60,'source_url':'https://a/2'})
        self.assertTrue(a&b);self.assertNotEqual(a,b)
    def test_known_representative_is_not_training_exposure_of_new_notice(self):
        old={'title':'과거 대표','body':'오래된 본문'*30,'source_url':'https://a/old'}
        new={'title':'신규 행사','body':'새로운 본문'*30,'source_url':'https://a/new'}
        self.assertFalse(fingerprints({'left':new,'right':old})&fingerprints(old))
