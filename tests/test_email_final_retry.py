import hashlib, json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import MagicMock
import requests
from together.plato.notifications import PlatoNotificationTransport, NotificationError

class FinalEmailRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session=MagicMock(); self.t=PlatoNotificationTransport.from_session(self.session,Path(self.temp.name))
        self.addCleanup(self.t.close)
        self.key='a'*64; self.event='event'
        event=hashlib.sha256(json.dumps(['email', self.event],ensure_ascii=False).encode()).hexdigest()
        self.t.ledger.claim(self.key,'email',1,event,['42'])
        self.t.ledger.finish(self.key,'unknown',1,'connection_reset')
        self.t.validated_recipients=MagicMock(return_value=({'form':'test'},['42'],[]))
        self.t.student_ids={'42':'student1'}
        self.t.email_history=MagicMock(return_value=[])
        self.payload={'event_key':self.event,'subject':'notice','body':'html','recipients':[],'expected_ids':['42']}
        self.attempt(13)

    def attempt(self,hours):
        self.t.attempt_path(self.key).write_text(json.dumps({'started_at':time.time()-hours*3600,
            'student_ids':['student1'],'subject':'notice','previous_email_ids':['9']}))

    def test_wait_from_actual_post_not_enqueue(self):
        self.attempt(11.99)
        self.assertIsNone(self.t.final_email_retry(self.payload))
        self.session.post.assert_not_called()

    def test_timeout_is_terminal_and_never_posts_a_third_time(self):
        self.session.post.side_effect=requests.Timeout()
        self.assertEqual(self.t.final_email_retry(self.payload),1)
        self.assertEqual(self.t.final_email_retry(self.payload),1)
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(self.t.ledger.connection.execute('select state,detail from deliveries').fetchone(),('sent','final_retry_assumed_unverified'))

    def test_late_receipt_avoids_retry(self):
        self.t.email_history.return_value=[{'subject':'notice','eid':'11','student_ids':{'student1'}}]
        self.assertEqual(self.t.final_email_retry(self.payload),1)
        self.session.post.assert_not_called()
        self.assertEqual(self.t.ledger.connection.execute('select detail from deliveries').fetchone()[0],'email_log:11')

    def test_restart_after_retry_reserved_cannot_submit_again(self):
        self.t.ledger.connection.execute('create table email_final_retries (delivery_id text primary key,started real,outcome text)')
        with self.t.ledger.connection as db:db.execute('insert into email_final_retries values(?,?,?)',(self.key,time.time(),'reserved_before_post'))
        self.assertEqual(self.t.final_email_retry(self.payload),1)
        self.session.post.assert_not_called()

    def test_changed_recipient_never_receives_old_notice(self):
        self.t.student_ids['42']='different'
        with self.assertRaises(NotificationError): self.t.final_email_retry(self.payload)
        self.session.post.assert_not_called()
