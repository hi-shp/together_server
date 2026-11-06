import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import requests
from together import config
from together.storage.scholarship_alerts import ScholarshipAlerts,alert_key

TITLE='[장학금] 2026년도 발전기금회계 목적사업「산학협력단 창업장학금」안내'

class ScholarshipDiscordTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.response=Mock();self.response.json.return_value={'id':'confirmed-receipt'}
  self.patches=[patch.object(config,'BASE_DIR',self.root),patch.dict(os.environ,{'WEBHOOK_URL':'https://example.invalid/webhook','DISCORD_MESSAGE_WEBHOOK_URL':'https://example.invalid/message','DISCORD_EMAIL_WEBHOOK_URL':'https://example.invalid/email'}),patch.object(config,'_discord_result'),patch.object(config.requests,'post',return_value=self.response)]
  for p in self.patches:self.addCleanup(p.stop)
  *_,self.post=[p.start() for p in self.patches]
 def test_same_scholarship_sent_once_across_restart_and_prefix_variation(self):
  self.assertTrue(config.notify(TITLE+'\n＞ 장학금'))
  self.assertFalse(config.notify(TITLE.replace('[장학금]','[공지] [장학]').replace('2026년도','2026년')+'\n＞ 장학금'))
  self.assertIsNone(ScholarshipAlerts(self.root).claim(TITLE));self.assertEqual(self.post.call_count,1)
 def test_different_year_semester_scholarship_and_date_remain_distinct(self):
  for a,b in [('2026년 창업장학금','2027년 창업장학금'),('2026년1학기 창업장학금','2026년2학기 창업장학금'),('2026년창업장학금','2026년창업지원장학금'),('2026년 장학금 11/1','2026년 장학금 1/11')]:self.assertNotEqual(alert_key(a),alert_key(b))
 def test_other_categories_and_subscriber_completion_logs_are_not_filtered(self):
  for _ in range(2):
   self.assertTrue(config.notify('행사\n＞ 교육/특강/프로그램'))
   self.assertTrue(config.notify(TITLE+'\n＞ 장학금',channel='DISCORD_MESSAGE_WEBHOOK_URL'))
   self.assertTrue(config.notify(TITLE+'\n＞ 장학금',channel='DISCORD_EMAIL_WEBHOOK_URL'))
  self.assertEqual(self.post.call_count,6)
 def test_unknown_post_is_not_automatically_repeated(self):
  self.post.side_effect=requests.ReadTimeout()
  self.assertFalse(config.notify(TITLE+'\n＞ 장학금'));self.assertFalse(config.notify(TITLE+'\n＞ 장학금'));self.assertEqual(self.post.call_count,1)
 def test_actual_old_receipt_can_seed_without_sending(self):
  ScholarshipAlerts(self.root).remember_sent(TITLE,1793955472,'old-receipt')
  self.assertFalse(config.notify(TITLE+'\n＞ 장학금'));self.post.assert_not_called()
 def test_disabled_webhook_does_not_retire_an_unsent_alert(self):
  with patch.dict(os.environ,{'WEBHOOK_URL':''}):self.assertFalse(config.notify(TITLE+'\n＞ 장학금'))
  self.assertTrue(config.notify(TITLE+'\n＞ 장학금'));self.assertEqual(self.post.call_count,1)
 def test_file_upload_is_not_filtered(self):
  p=self.root/'log.txt';p.write_text('complete cycle')
  for _ in range(2):self.assertTrue(config.notify(TITLE+'\n＞ 장학금',log_path=p))
  self.assertEqual(self.post.call_count,2)
