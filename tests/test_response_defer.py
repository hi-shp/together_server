import unittest
import types
from unittest.mock import Mock, patch
import requests
from selenium.common.exceptions import TimeoutException
from together.crawler import announcements, onestop, detail_recovery
from together.operations import state

class ResponseDeferTests(unittest.TestCase):
    def test_detail_budget_does_not_start_another_request(self):
        with patch.object(detail_recovery.time, 'monotonic', return_value=61), patch.object(detail_recovery.requests, 'Session') as session:
            result=detail_recovery.probe('https://law.pusan.ac.kr/bbs/law/482/123/artclView.do', 'https://law.pusan.ac.kr/law/10741/subview.do', deadline=60)
        self.assertEqual(result['reason'], 'Timeout')
        session.return_value.__enter__.return_value.get.assert_not_called()

    def test_source_network_failure_is_not_warning(self):
        for fn,args in [(announcements.crawl_ann_partial,('https://x.pusan.ac.kr/a',)),(announcements.crawl_ann,('https://x.pusan.ac.kr/a','교육/특강/프로그램'))]:
            with patch.object(announcements, 'http_get', side_effect=requests.ReadTimeout()), patch.object(announcements, 'event') as logged:
                self.assertIsNone(fn(*args))
                self.assertEqual(logged.call_args.kwargs['level'],'INFO')

    def test_authenticated_timeout_keeps_cursor_and_not_error(self):
        page=types.SimpleNamespace(page_url='https://onestop.pusan.ac.kr/page?menuCD=000000000000062',department='장학',number=10)
        for error in (requests.Timeout(),TimeoutException()):
            with patch.object(onestop,'client') as client, patch.object(state,'record_board_deferred') as deferred:
                client.return_value.recent.side_effect=error
                logged=Mock();failed=Mock()
                self.assertEqual(onestop.collect(page,3,logged,failed),([],10))
                failed.assert_not_called();deferred.assert_called_once()
                self.assertNotIn(logged.call_args.kwargs.get('level'),{'WARN','ERROR'})

    def test_detail_deferred_does_not_increment_checked_or_failed(self):
        with patch.object(state, '_boards', {'checked':1,'failed':0}):
            state.record_detail_deferred()
            self.assertEqual(state.board_summary(),{'checked':1,'failed':0,'detail_deferred':1})


    def test_scholarship_cannot_be_merged_as_enrichment(self):
        from together.notices.intelligence import enrichment
        notice=types.SimpleNamespace(title='우수학생 국가장학사업 추천 안내')
        with patch.object(enrichment, 'PublishedNoticeStore') as store:
            self.assertIsNone(enrichment.find_target(notice, '학과', '/unused'))
            store.assert_not_called()

if __name__=='__main__':unittest.main()
