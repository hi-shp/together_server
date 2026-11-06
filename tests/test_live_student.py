import json,tempfile,time,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch,MagicMock
from types import SimpleNamespace
from together.learning.campaign import Campaign
from together.notices.intelligence.prompts import category_request,duplicate_request
from together.storage.api_usage import ApiAccounting

class CampaignTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.root=self.base/'.runtime/student';self.root.mkdir(parents=True)
  self.at=datetime.fromisoformat('2026-11-06T18:00:00+09:00').timestamp()
  self.config={'enabled':True,'compare_until':'2026-11-30','balance_observed_at':self.at,'grant_balance_usd':15.69,'reserve_usd':5,'prior_unsettled_usd':0,'review_below':{'notice_category':.35,'duplicate_decision':.45}}
  (self.root/'campaign.json').write_text(json.dumps(self.config));(self.root/'active.json').write_text(json.dumps({'directory':'v1'}));(self.root/'previous-source-hashes.json').write_text('[]')
  self.student=MagicMock();self.student.category.return_value={'label':'해당없음','confidence':.9,'margin':1,'model_version':'v1'};self.student.pair.return_value={'label':'same_event','confidence':.98,'model_version':'v1'}
  self.patch=patch('together.learning.campaign.model',return_value=self.student);self.patch.start();self.addCleanup(self.patch.stop)
  self.request=category_request('프로그램 신청',['해당없음','교육/특강/프로그램'],'신청 본문')
  self.c=Campaign(self.base,self.at)
 def test_november_compares_even_confident_results(self):
  obs=self.c.begin(self.request,{'source_url':'https://example.invalid/1'})
  self.assertTrue(self.c.wants_teacher(obs))
  self.c.finish(obs,{'category':'교육/특강/프로그램'},'api')
  with self.c.db() as db:
   p,t=db.execute('SELECT prediction,teacher FROM observations').fetchone()
  self.assertEqual(json.loads(p)['label'],'해당없음');self.assertEqual(json.loads(t)['category'],'교육/특강/프로그램')
 def test_december_switches_by_korean_date_not_daily_quota(self):
  c=Campaign(self.base,datetime.fromisoformat('2026-12-01T00:00:00+09:00').timestamp());obs=c.begin(self.request,{})
  self.assertFalse(c.comparing);self.assertFalse(c.wants_teacher(obs));obs['prediction']['confidence']=.2;self.assertTrue(c.wants_teacher(obs));self.assertEqual(c.reserve(self.request),'not_campaign')
 def test_prediction_cannot_be_rewritten_after_seeing_teacher(self):
  obs=self.c.begin(self.request,{});self.c.finish(obs,{'category':'교육/특강/프로그램'},'api');self.student.category.return_value=dict(self.student.category.return_value,label='교육/특강/프로그램');self.c.begin(self.request,{})
  with self.c.db() as db:self.assertEqual(json.loads(db.execute('SELECT prediction FROM observations').fetchone()[0])['label'],'해당없음')
 def test_fallback_never_becomes_a_teacher_label(self):
  obs=self.c.begin(self.request,{})
  self.c.finish(obs,{'category':'해당없음','_local_fallback':True},'cache')
  with self.c.db() as db:self.assertEqual(db.execute('SELECT teacher FROM observations').fetchone()[0],'')
 def test_reserve_stops_extra_comparison_not_crawler(self):
  self.c.config['grant_balance_usd']=5.00001
  self.assertIsNone(self.c.reserve(self.request));obs=self.c.begin(self.request,{});self.assertEqual(self.c.local_result(obs)['category'],'해당없음')
 def test_inflight_and_unknown_requests_remain_reserved(self):
  key=self.c.reserve(self.request);self.assertTrue(key);self.c.settled(key,None)
  with self.c.db() as db:self.assertEqual(db.execute('SELECT state FROM reservations').fetchone()[0],'pending')
  self.c.settled(key,SimpleNamespace(usage=SimpleNamespace()))
  with self.c.db() as db:self.assertEqual(db.execute('SELECT state FROM reservations').fetchone()[0],'settled')
 def test_high_confidence_title_only_duplicate_cannot_suppress_first_notice(self):
  request=duplicate_request('장기 행사',['장기 행사']);obs=self.c.begin(request,{})
  self.assertEqual(self.c.local_result(obs)['relation'],'needs_context')
 def test_disabled_campaign_does_not_load_model(self):
  (self.root/'campaign.json').unlink();c=Campaign(self.base,self.at);self.assertIsNone(c.begin(self.request,{}));self.student.category.assert_not_called()
 def test_api_decision_uses_teacher_on_disagreement(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('live_api_test',Path(__file__).parents[1]/'together/integrations/openai_client.py')
  api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
  teacher={'category':'교육/특강/프로그램','notice_type':'notice','needs_body':False,'reason':'참여 프로그램'}
  response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(teacher)),finish_reason='stop')],usage=SimpleNamespace(prompt_tokens=50,completion_tokens=20,prompt_tokens_details=None))
  with patch.object(api,'BASE_DIR',self.base),patch.object(api.client.chat.completions,'create',return_value=response) as create,patch.object(api,'_retry_api_at',0),patch('together.learning.campaign.time.time',return_value=self.at):
   result=api._decision(api.CATEGORY_MODEL,self.request);self.assertEqual(result['category'],teacher['category']);create.assert_called_once()
 def test_cached_local_result_does_not_pose_as_gpt_reference(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('live_api_test',Path(__file__).parents[1]/'together/integrations/openai_client.py')
  api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
  ApiAccounting(self.base).store(api.CATEGORY_MODEL,self.request,{'category':'해당없음','_local_fallback':True})
  with patch.object(api,'BASE_DIR',self.base),patch.object(api.client.chat.completions,'create',side_effect=ConnectionError('offline')) as create,patch.object(api,'_retry_api_at',0),patch('together.learning.campaign.time.time',return_value=self.at):
   result=api._decision(api.CATEGORY_MODEL,self.request);create.assert_called_once();self.assertTrue(result['_local_fallback'])

 def test_unknown_model_price_does_not_spend_reserved_credit(self):
  self.assertIsNone(self.c.reserve(self.request,"unknown-model"))

 def test_december_uncertain_evidence_is_referred_even_with_high_model_score(self):
  c=Campaign(self.base,datetime.fromisoformat('2026-12-01T00:00:00+09:00').timestamp())
  obs=c.begin(duplicate_request('장기 행사',['장기 행사']),{})
  self.assertGreater(obs['prediction']['confidence'],.9)
  self.assertTrue(c.wants_teacher(obs))
