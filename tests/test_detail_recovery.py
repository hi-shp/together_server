import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from bs4 import BeautifulSoup
from together.crawler.detail_recovery import identity,listing,probe,replacement,remember_limit
from together.storage.pending_notices import PendingNotices

B='https://law.pusan.ac.kr/law/10741/subview.do'
U='https://law.pusan.ac.kr/bbs/law/482/123/artclView.do'
V=U.replace('/123/','/124/')

def html(link,title='대학원 모집',day='2026-11-05'):
    return f'<table><tr><td><a href="{link}">{title}</a></td><td class="td-date">{day}</td><td class="td-write">기관</td></tr></table>'

def response(url,s):
    r=MagicMock();r.url=url;r.content=s.encode();r.status_code=200;return r

class DetailRecoveryTests(unittest.TestCase):
    def test_expiry_from_first_failure_with_last_attempt_and_no_rediscovery(self):
        with tempfile.TemporaryDirectory() as root, patch('together.storage.pending_notices.time.time') as clock:
            clock.return_value=10
            p=PendingNotices(root);p.add(B,U,'학과')
            self.assertFalse(p.mark_source_failure(B,U,'no',can_expire=True))
            clock.return_value=43209
            self.assertFalse(p.mark_source_failure(B,U,'no',can_expire=True))
            clock.return_value=43210
            self.assertTrue(p.mark_source_failure(B,U,'no',can_expire=True))
            self.assertFalse(PendingNotices(root).add(B,U,'학과'))
            self.assertTrue(p.add(B,V,'학과'))

    def test_uncertain_delivery_is_never_expired_and_recovery_resets_clock(self):
        with tempfile.TemporaryDirectory() as root, patch('together.storage.pending_notices.time.time') as clock:
            clock.return_value=100
            p=PendingNotices(root);p.add(B,U,'학과');p.mark_source_failure(B,U,'no')
            p.recovered_source(B,U)
            clock.return_value=50000
            self.assertFalse(p.mark_source_failure(B,U,'no',can_expire=True))
            p.mark_notifications(B,U);clock.return_value=100000
            self.assertFalse(p.expire_unreadable(B,U))

    def test_summary_excludes_previously_sent_and_idle_cycles(self):
        from together.operations.cycle_activity import summarize
        with tempfile.TemporaryDirectory() as root:
            j=Path(root)/'activity';before={('one','message')}
            self.assertEqual(summarize(j,before,before,0),'')
            j.write_text('new\npost\nenriched\nwarn\n')
            headline=summarize(j,before,before|{('two','email')},0)
            self.assertTrue(headline.startswith('⭐ 새 공지 1건'))
            for part in ('이메일 발송 1건','쪽지 발송 0건','보강 1건','오류 0회','경고 1회'):
                self.assertIn(part,headline)

    def test_official_search_recovers_renumbered_row_without_writer(self):
        form='<form name="searchForm" action="/bbs/law/482/artclList.do"><input name="findWord" value="대학원 모집"></form>'
        entry=html(V).replace('<td class="td-write">기관</td>','<td class="td-num">57</td>')
        original=listing(BeautifulSoup(entry,'html.parser'),B)[V]
        with patch('together.crawler.detail_recovery.requests.Session') as factory:
            get=factory.return_value.__enter__.return_value.get
            get.side_effect=[response(B,form),response(B,form+entry),response(V,'<h4 class="vtitle">대학원 모집</h4><div class="txt">내용</div>')]
            self.assertEqual(probe(U,B,original)['link'],V)
    def test_official_encoded_alias_and_referrer_use_same_session(self):
        alias=B+'?enc='+base64.b64encode(b'x|@@|/bbs/law/482/123/artclView.do').decode()
        self.assertEqual(identity(alias),identity(U))
        with patch('together.crawler.detail_recovery.requests.Session') as factory:
            get=factory.return_value.__enter__.return_value.get
            get.side_effect=[response(B,html(alias)),response(alias,'<h4 class="vtitle">대학원 모집</h4><div class="txt">내용</div>')]
            out=probe(U,B)
            self.assertEqual(out['link'],alias)
            self.assertEqual(get.call_args.kwargs['headers']['Referer'],B)

    def test_move_requires_unique_board_title_date_and_writer(self):
        rows=listing(BeautifulSoup(html(V),'html.parser'),B);original=rows[V]
        self.assertEqual(replacement(U,original,rows),V)
        self.assertIsNone(replacement(U,dict(original,day='2026-11-06'),rows))
        self.assertIsNone(replacement(U,dict(original,writer_hash=''),rows))
        self.assertIsNone(replacement(U,original,{**rows,V.replace('/124/','/126/'):original}))
        self.assertIsNone(replacement(U,original,{V.replace('/482/','/7/'):original}))

    def test_explicit_access_denial_does_not_invent_an_article(self):
        with patch('together.crawler.detail_recovery.requests.Session') as factory:
            get=factory.return_value.__enter__.return_value.get
            get.side_effect=[response(B,html(V)),response(U,'<form class="_messageForm">게시물에 접근할 수 없습니다.</form>')]
            r=probe(U,B)
            self.assertFalse(r['listed']);self.assertEqual(r['reason'],'school_access_message');self.assertNotIn('response',r)

    def test_retry_after_prevents_immediate_repeat(self):
        r=MagicMock(status_code=429,headers={'Retry-After':'900'});remember_limit(U,r)
        with patch('together.crawler.detail_recovery.requests.Session') as factory:
            self.assertGreaterEqual(probe(U,B)['retry_after'],899);factory.assert_not_called()

    def test_relocated_notice_waits_for_real_completion_and_preserves_old_record(self):
        with tempfile.TemporaryDirectory() as root:
            p=PendingNotices(root);p.add(B,U,'학과')
            self.assertFalse(p.follow_relocated(B,U,V,'학과'));self.assertFalse(p.follow_relocated(B,U,V,'학과'))
            self.assertEqual(len(p.for_board(B)),2)
            p.finish(B,V);self.assertTrue(p.follow_relocated(B,U,V,'학과'));self.assertFalse(p.for_board(B))
