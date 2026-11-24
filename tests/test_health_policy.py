import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.health_check import delivery_metrics, evaluate
from together.operations.health_policy import work_credit_fallback_ready


class HealthPolicyTests(unittest.TestCase):
    def test_inode_pressure_is_detected_even_when_disk_bytes_are_available(self):
        metrics={'free_bytes':10**11, 'inodes':{'total':100000,'available':999}}
        self.assertIn('inode_critical',{row['code'] for row in evaluate(metrics)[0]})
        self.assertEqual(evaluate(dict(metrics,inodes={'total':0,'available':0}))[0],[])
        self.assertEqual(evaluate(dict(metrics,inodes={'total':100000,'available':2000}))[0],[])

    def prepare_mail(self, root, started):
        runtime = root/'.runtime'
        attempts = runtime/'notification-attempts'
        attempts.mkdir(parents=True)
        (attempts/'email1.json').write_text(json.dumps({'started_at': started}))
        event = hashlib.sha256(json.dumps(['email','event1']).encode()).hexdigest()
        with sqlite3.connect(runtime/'notification-deliveries.sqlite3') as db:
            db.execute('CREATE TABLE deliveries (id TEXT,kind TEXT,state TEXT,updated REAL,recipients INTEGER)')
            db.execute('CREATE TABLE delivery_recipients (event TEXT,kind TEXT,uid TEXT,state TEXT,delivery_id TEXT)')
            db.executemany('INSERT INTO deliveries VALUES (?,?,?,?,?)', [
                ('email1','email','unknown',1000,50),('message1','message','unknown',1000,20)])
            db.execute('INSERT INTO delivery_recipients VALUES (?,?,?,?,?)',(event,'email','member','unknown','email1'))
        with sqlite3.connect(runtime/'email-outbox.sqlite3') as db:
            db.execute('CREATE TABLE jobs (state TEXT,recipients INTEGER,created REAL,updated REAL,payload TEXT)')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('unknown',50,500,1000,json.dumps({'event_key':'event1'})))

    def test_original_attempt_controls_grace_not_job_creation_or_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.prepare_mail(root,9000)
            result=delivery_metrics(root,now=10000)
            self.assertEqual(result['unknown_delivery_recipients'],20)  # Messages stay actionable.
            self.assertEqual(result['email_queue_pending'],1)
            self.assertEqual(result['email_queue_blocked'],0)
            self.assertEqual(result['email_queue_idle_seconds'],0)
            self.assertEqual(result['email_confirmation_waiting_recipients'],50)
            late=delivery_metrics(root,now=9000+12*3600)
            self.assertEqual(late['unknown_delivery_recipients'],70)
            self.assertEqual(late['email_queue_blocked'],50)
            self.assertGreater(late['email_queue_idle_seconds'],1800)

    def test_missing_original_receipt_never_starts_a_fresh_grace(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.prepare_mail(root,9000)
            (root/'.runtime/notification-attempts/email1.json').unlink()
            result=delivery_metrics(root,now=10000)
            self.assertEqual(result['unknown_delivery_recipients'],70)
            self.assertEqual(result['email_queue_blocked'],50)

    def test_actual_new_backlog_or_stopped_worker_still_alerts_during_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.prepare_mail(root,9000)
            with sqlite3.connect(root/'.runtime/email-outbox.sqlite3') as db:
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('pending',1,1000,1000,'{}'))
            metrics=delivery_metrics(root,now=10000)
            metrics.update(free_bytes=10**11,email_worker='inactive')
            codes={row['code'] for row in evaluate(metrics)[0]}
            self.assertIn('email_queue_stalled',codes)
            self.assertIn('email_worker_stopped',codes)

    def test_credit_expiry_only_suppressed_with_recent_work_success(self):
        metrics=dict(free_bytes=10**11,budget=dict(estimated_remaining_usd=.1,forecast_days=1,expires_in_days=1),
                     api={'errors_last_day':{'insufficient_quota':1,'invalid_api_key':1}})
        codes=lambda m:{row['code'] for row in evaluate(m)[0]}
        self.assertIn('api_credit_expiry',codes(metrics))
        safe=codes(dict(metrics,work_credit_fallback_ready=True))
        self.assertEqual(safe,{'api_invalid_api_key'})
        self.assertIn('api_credit_expiry',codes(dict(metrics,work_credit_fallback_ready=False)))

    def test_work_files_alone_are_not_a_success_or_permission_to_hide_alarm(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runtime=root/'.runtime';auth=runtime/'work-codex';auth.mkdir(parents=True)
            binary=root/'command';binary.touch();(auth/'auth.json').touch()
            (runtime/'decision-provider.json').write_text(json.dumps({'policy':'api_credit_then_work','work_binary':str(binary)}))
            self.assertFalse(work_credit_fallback_ready(root,now=10000))
            with sqlite3.connect(auth/'decisions.sqlite3') as db:
                db.execute('CREATE TABLE attempts (at REAL,state TEXT)')
                db.execute("INSERT INTO attempts VALUES (9000,'complete')")
            self.assertTrue(work_credit_fallback_ready(root,now=10000))
            self.assertFalse(work_credit_fallback_ready(root,now=9000+31*86400))
            with sqlite3.connect(auth/'decisions.sqlite3') as db:
                db.execute("INSERT INTO attempts VALUES (9500,'unconfirmed')")
            self.assertFalse(work_credit_fallback_ready(root,now=10000))


if __name__=='__main__':
    unittest.main()
