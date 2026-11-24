from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from together.operations.nightly import KST,activity,advance,read,run,scheduled_slot,verdict
from together.operations.nightly_http import cached_response
from together.operations.nightly_semantic import batches,collect


class ScheduleTests(unittest.TestCase):
    def test_friday_runs_at_2200_korea_not_utc(self):
        now=datetime(2026,11,27,13,0,tzinfo=timezone.utc)
        self.assertEqual(scheduled_slot(now).isoformat(),'2026-11-27T22:00:00+09:00')

    def test_saturday_and_sunday_never_start_even_manual(self):
        for day in (28,29):
            now=datetime(2026,11,day,22,tzinfo=KST)
            self.assertIsNone(scheduled_slot(now))
            self.assertIsNone(scheduled_slot(now,True))

    def test_boot_monday_morning_recovers_friday_slot(self):
        self.assertEqual(scheduled_slot(datetime(2026,11,30,8,tzinfo=KST)).isoformat(),'2026-11-27T22:00:00+09:00')

    def test_manual_verification_does_not_claim_the_evening_slot(self):
        now=datetime(2026,11,24,18,5,tzinfo=KST)
        self.assertEqual(scheduled_slot(now,True),now)
        self.assertEqual(scheduled_slot(now).day,23)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.slot=datetime(2026,11,24,22,tzinfo=KST)

    def observations(self,rows):
        p=self.base/'.runtime/intelligence/observations.sqlite3';p.parent.mkdir(parents=True)
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE notices(source_url TEXT,observed_at TEXT)')
            db.executemany('INSERT INTO notices VALUES (?,?)',rows)

    def test_activity_is_unique_first_seen_source_not_observation_count(self):
        self.observations([('old','2026-11-23T23:00:00'),('old','2026-11-24T12:00:00'),('new','2026-11-24T12:00:00'),('new','2026-11-24T13:00:00')])
        self.assertEqual(activity(self.base,self.slot),1)

    def test_pruned_observations_cannot_make_old_sources_look_new(self):
        self.observations([('old','2026-11-24T12:00:00')])
        p=self.base/'.runtime/intelligence/collection/index.sqlite3';p.parent.mkdir(parents=True)
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE samples(source_url TEXT,first_seen TEXT)')
            db.execute('INSERT INTO samples VALUES (?,?)',('old','2026-11-20T10:00:00+09:00'))
        self.assertEqual(activity(self.base,self.slot),0)

    def test_unpublished_pending_source_also_triggers_audit(self):
        self.observations([])
        (self.base/'.runtime/pending-notices.json').write_text(json.dumps({'pending':[{'source_url':'not-yet-published','created_at':self.slot.timestamp()-60}]}))
        self.assertEqual(activity(self.base,self.slot),1)

    def test_missing_evidence_is_unknown_not_a_quiet_zero(self):
        self.assertIsNone(activity(self.base,self.slot))

    def test_no_activity_does_not_run_helpers_or_work(self):
        self.observations([])
        with patch('together.operations.nightly.subprocess.run') as execute:
            result=run(self.base,now=self.slot)
            self.assertEqual(result['status'],'skipped_no_new_notices')
            execute.assert_not_called()
        self.assertFalse((self.base/'.runtime/nightly-audit/state.json').exists())

    def test_missing_url_or_receipt_does_not_advance_its_cursor(self):
        state={'cursors':{'messages':100}}
        advance(state,'messages',200,False)
        self.assertEqual(state['cursors']['messages'],100)
        advance(state,'messages',200,True)
        self.assertEqual(state['cursors']['messages'],200)

    def test_partial_url_inventory_is_not_complete(self):
        self.assertFalse(verdict('links',{'checked':99,'physical_boards':100,'results':[{'result':'verified'}]}))
        self.assertFalse(verdict('links',{'checked':1,'physical_boards':1,'results':[{'result':'response_deferred'}]}))

    def test_duplicate_recipients_preserve_unverified_range(self):
        self.assertFalse(verdict('messages',{'expected_events':1,'verified_events':1,'duplicates':[{'event':'x'}]}))

    def test_batched_review_never_drops_or_reorders_evidence(self):
        rows=[{'id':str(i),'request':{'text':'a'*2500}} for i in range(19)]
        groups=list(batches(rows,maximum=8000))
        self.assertEqual([r['id'] for g in groups for r in g],[str(i) for i in range(19)])
        self.assertTrue(all(len(g)<=16 for g in groups))

    def test_audit_work_does_not_share_the_live_execution_lock(self):
        from together.integrations.work_client import WorkClient
        live=WorkClient(self.base,{})
        audit=WorkClient(self.base,{},audit=True)
        self.assertNotEqual(live.root,audit.root)
        self.assertEqual(live.auth_home,audit.auth_home)
        self.assertEqual(live.root,self.base/'.runtime/work-codex')

    def test_larger_audit_batch_never_expands_live_batch_limits(self):
        from together.integrations.work_client import WorkClient
        live=WorkClient(self.base,{})
        with self.assertRaises(ValueError):live.decide_many([{}]*9)
        audit=WorkClient(self.base,{},audit=True)
        with self.assertRaises(ValueError):audit.decide_many([{}]*17)
        self.assertFalse(live.audit)
        self.assertTrue(audit.audit)

    def test_snapshot_reuse_preserves_exact_bytes_and_encoding(self):
        url='https://sample.pusan.ac.kr/test/1/subview.do';key=hashlib.sha256(url.encode()).hexdigest()
        raw='<table><tbody><tr><td>새 공지 &amp; 본문</td></tr></tbody></table>'.encode()
        p=self.base/(key+'.gz')
        with gzip.open(p,'wb') as stream:stream.write(raw)
        (self.base/(key+'.json')).write_text(json.dumps({'url':url,'status':200,'final_url':url,'content_type':'text/html; charset=utf-8'}))
        response=cached_response(self.base,url,now=p.stat().st_mtime+10)
        self.assertEqual(response.content,raw)
        self.assertEqual(response.text,raw.decode())
        self.assertIsNone(cached_response(self.base,url,now=p.stat().st_mtime+6*3600+1))

    def pair_sample(self,label,confidence,teacher):
        p=self.base/'.runtime/student/comparisons.sqlite3';p.parent.mkdir(parents=True)
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE observations(id TEXT,at REAL,task TEXT,payload TEXT,teacher TEXT,context TEXT,prediction TEXT)')
            data={'left':{'title':'공통 프로그램 신청','body':'학생 프로그램','source_url':'https://a.pusan.ac.kr/1'},'right':{'title':'공통 프로그램 참가','body':'학생 프로그램','source_url':'https://b.pusan.ac.kr/2'}}
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?)',('x',100,'duplicate_decision',json.dumps(data),json.dumps({'relation':teacher}),json.dumps({'source_url':'https://a.pusan.ac.kr/1'}),json.dumps({'label':label,'confidence':confidence})))

    def test_suppressed_first_notice_candidates_are_always_reviewed(self):
        self.pair_sample('same_event',.999,'same_event')
        rows=collect(self.base,{'semantic_since':0,'until':200})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['expected']['relation'],'same_event')

    def test_obvious_rejected_pairs_do_not_get_another_work_request(self):
        self.pair_sample('different_event',.999,'different_event')
        self.assertEqual(collect(self.base,{'semantic_since':0,'until':200}),[])

    def test_category_review_uses_final_label_and_no_future_body(self):
        p=self.base/'.runtime/intelligence/observations.sqlite3';p.parent.mkdir(parents=True)
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE notices(id INTEGER,session_id TEXT,observed_at TEXT,source_url TEXT,title TEXT,body_excerpt TEXT)')
            db.execute('CREATE TABLE categories(id INTEGER,session_id TEXT,observed_at TEXT,title TEXT,actual_category TEXT)')
            db.executemany('INSERT INTO notices VALUES (?,?,?,?,?,?)',[(1,'s','2026-11-24T10:00:00','u','제목','과거 본문'),(2,'s','2026-11-24T23:00:00','u','제목','미래 본문')])
            db.executemany('INSERT INTO categories VALUES (?,?,?,?,?)',[(1,'s','2026-11-24T10:00:00','제목','해당없음'),(2,'s','2026-11-24T11:00:00','제목','교육/특강/프로그램')])
        rows=collect(self.base,{'semantic_since':self.slot.timestamp()-86400,'until':self.slot.timestamp()})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['expected']['category'],'교육/특강/프로그램')
        self.assertEqual(json.loads(rows[0]['request']['messages'][-1]['content'])['body'],'과거 본문')

    def test_error_or_unresolved_routing_snapshot_is_never_a_healthy_cache_hit(self):
        url='https://sample.pusan.ac.kr/test/1/subview.do';key=hashlib.sha256(url.encode()).hexdigest()
        with gzip.open(self.base/(key+'.gz'),'wb') as stream:stream.write(b'not a board')
        (self.base/(key+'.json')).write_text(json.dumps({'url':url,'status':200,'routing_unresolved':True}))
        self.assertIsNone(cached_response(self.base,url))


if __name__=='__main__':unittest.main()
