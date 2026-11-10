import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup
from together.crawler import announcements as crawler
from together.crawler.list_recovery import recover_list, direct_list_url

SOURCE = 'https://gtu.pusan.ac.kr/gtu/63580/subview.do'
TARGET = 'https://gtu.pusan.ac.kr/bbs/gtu/15982/artclList.do'
SHELL = '<main>fnctId=bbs,fnctNo=15982 원하시는 페이지를 찾을 수가 없습니다.</main>'
NORMAL = '<form action="/bbs/gtu/15982/artclList.do"><table><th class="_artclThNum">번호</th><tbody><tr><td class="_artclTdNum">19</td><td class="_artclTdTitle"><a class="artclLinkView" href="/bbs/gtu/15982/1471000/artclView.do">새 글</a></td></tr></tbody></table></form>'


class DirectListRecovery(unittest.TestCase):
    def test_verified_direct_overrides_keep_the_original_csv_identity_and_cursor(self):
        from together.crawler.pages import AnnouncementPage
        pairs=[('inslab.pusan.ac.kr/enslab/26819','inslab.pusan.ac.kr/bbs/enslab/5512'),
               ('inslab.pusan.ac.kr/enslab/49684','inslab.pusan.ac.kr/bbs/enslab/11807'),
               ('srin.pusan.ac.kr/srin/39356','srin.pusan.ac.kr/bbs/srin/8371'),
               ('srin.pusan.ac.kr/srin/39358','srin.pusan.ac.kr/bbs/srin/8372'),
               ('srin.pusan.ac.kr/srin/39361','srin.pusan.ac.kr/bbs/srin/10380')]
        for source,target in pairs:
            original='https://'+source+'/subview.do'
            page=AnnouncementPage('기존 구독명',original,'https://'+source.split('/')[0],59)
            self.assertEqual((page.source_url,page.department,page.number),(original,'기존 구독명',59))
            self.assertEqual(page.page_url,'https://'+target+'/artclList.do')

    def test_explicit_disabled_direct_module_is_separate_from_network_defer(self):
        source='https://srin.pusan.ac.kr/bbs/srin/8371/artclList.do'
        page=types.SimpleNamespace(page_url=source,department='기존 구독명',number=59)
        response=Mock(text='<html><body>사용중지된 싱글 모듈입니다.</body></html>',url=source,status_code=200)
        with patch.object(crawler,'http_get',return_value=response),patch.object(crawler,'record_board_result') as state,patch.object(crawler,'record_board_deferred') as deferred,patch.object(crawler,'event'):
            self.assertEqual(crawler.get_anns_url(page,1),([],59))
        state.assert_called_once_with(None);deferred.assert_not_called()

    def test_transient_http_failure_is_deferred_but_permanent_http_failure_is_not_hidden(self):
        page = types.SimpleNamespace(page_url=SOURCE, department='GTU', number=18)
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status), patch.object(crawler, 'http_get', return_value=Mock(status_code=status)), patch.object(crawler.time, 'sleep'), patch.object(crawler, 'record_board_deferred') as deferred, patch.object(crawler, 'record_board_result') as failed, patch.object(crawler, 'event') as event:
                self.assertEqual(crawler.get_anns_url(page, 1), ([], 18))
                deferred.assert_called_once_with(); failed.assert_not_called()
                self.assertFalse(any(c.kwargs.get('level') in {'WARN', 'ERROR'} for c in event.call_args_list))
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = crawler.requests.HTTPError(response=response)
        with patch.object(crawler, 'http_get', return_value=response), patch.object(crawler, 'record_board_result') as failed, patch.object(crawler, 'record_board_deferred') as deferred, patch.object(crawler, 'event'):
            self.assertEqual(crawler.get_anns_url(page, 1), ([], 18))
            failed.assert_called_once_with(False); deferred.assert_not_called()

    def test_pending_prefetch_is_bounded_and_not_counted_as_error(self):
        page = types.SimpleNamespace(page_url=SOURCE, department='GTU', number=18)
        future = Mock(); future.result.side_effect = TimeoutError()
        with patch.object(crawler, 'record_board_deferred') as deferred, patch.object(crawler, 'record_board_result') as failed, patch.object(crawler, 'event') as event:
            self.assertEqual(crawler.get_anns_url(page, 1, future), ([], 18))
        self.assertLessEqual(future.result.call_args.kwargs['timeout'], 60)
        deferred.assert_called_once_with(); failed.assert_not_called()
        self.assertFalse(any(c.kwargs.get('level') in {'WARN', 'ERROR'} for c in event.call_args_list))
        self.assertIsNone(crawler._board_deadline.get())

    def test_remaining_window_limits_network_timeout_and_prevents_extra_request(self):
        token = crawler._board_deadline.set(20)
        response = Mock()
        try:
            with patch.object(crawler.time, 'monotonic', return_value=19), patch.object(crawler, 'http_get', return_value=response) as fetch:
                crawler._get_board_response(SOURCE)
                self.assertLessEqual(sum(fetch.call_args.kwargs['timeout']), 1)
            with patch.object(crawler.time, 'monotonic', return_value=20), patch.object(crawler, 'http_get') as fetch:
                with self.assertRaises(crawler.requests.Timeout):
                    crawler._get_board_response(SOURCE)
                fetch.assert_not_called()
        finally:
            crawler._board_deadline.reset(token)

    def test_recovers_same_board_new_notice_without_warning(self):
        shell = types.SimpleNamespace(text=SHELL, url=SOURCE)
        normal = types.SimpleNamespace(text=NORMAL, url=TARGET)
        page = types.SimpleNamespace(page_url=SOURCE, department='GTU', number=18)
        with tempfile.TemporaryDirectory() as root, patch.object(crawler, 'BASE_DIR', Path(root)), patch.object(crawler, '_get_board_response', side_effect=[shell, shell, shell, normal]) as fetch, patch.object(crawler, 'record_board_result') as state, patch.object(crawler, 'event') as event:
            result = crawler.get_anns_url(page, 1)
        self.assertEqual(result, (['https://gtu.pusan.ac.kr/bbs/gtu/15982/1471000/artclView.do'], 19))
        self.assertEqual(fetch.call_args.args, (TARGET,))
        state.assert_called_once_with(True)
        self.assertFalse(any(c.kwargs.get('level') == 'WARN' for c in event.call_args_list))

    def test_persistent_failure_is_deferred_without_warning_and_keeps_cursor(self):
        shell = types.SimpleNamespace(text=SHELL, url=SOURCE)
        page = types.SimpleNamespace(page_url=SOURCE, department='GTU', number=18)
        with tempfile.TemporaryDirectory() as root, patch.object(crawler, 'BASE_DIR', Path(root)), patch.object(crawler, '_get_board_response', return_value=shell), patch.object(crawler, 'record_board_deferred') as state, patch.object(crawler, 'event') as event:
            self.assertEqual(crawler.get_anns_url(page, 1), ([], 18))
        state.assert_called_once_with()
        self.assertEqual(sum(c.kwargs.get('level') == 'WARN' for c in event.call_args_list), 0)

    def test_redirect_wrong_board_or_unrelated_table_never_counts_as_success(self):
        for response in [types.SimpleNamespace(text=NORMAL, url=SOURCE), types.SimpleNamespace(text=NORMAL.replace('/15982/', '/999/'), url=TARGET), types.SimpleNamespace(text='<tbody><tr><td>home</td></tr></tbody>', url=TARGET)]:
            self.assertIsNone(recover_list(SOURCE, BeautifulSoup(SHELL, 'html.parser'), Mock(return_value=response)))

    def test_confirmed_empty_same_board_is_valid(self):
        html = '<form action="/bbs/gtu/15982/artclList.do"><th class="_artclThNum">번호</th><tbody><tr><td>게시물이(가) 없습니다.</td></tr></tbody></form>'
        self.assertIsNotNone(recover_list(SOURCE, BeautifulSoup(SHELL, 'html.parser'), Mock(return_value=types.SimpleNamespace(text=html, url=TARGET))))

    def test_external_or_ambiguous_board_identifiers_are_not_followed(self):
        self.assertIsNone(direct_list_url(SOURCE.replace('gtu.pusan.ac.kr', 'other.invalid'), BeautifulSoup(SHELL, 'html.parser')))
        self.assertIsNone(direct_list_url(SOURCE, BeautifulSoup(SHELL + 'fnctId=bbs,fnctNo=99', 'html.parser')))


if __name__ == '__main__':
    unittest.main()
