import contextlib
import io
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock,patch
from together.crawler import announcements


class BoardShellRecovery(unittest.TestCase):
    def test_two_shell_responses_use_fresh_connection_before_recording_failure(self):
        url='https://smartfactory.pusan.ac.kr/smartfactory/34441/subview.do'
        page=types.SimpleNamespace(page_url=url,source_url=url,department='센터 공지',number=10)
        shell=types.SimpleNamespace(text='<html><title>센터공지사항</title><main>자료실</main></html>',url=url)
        normal=types.SimpleNamespace(text='<tbody><tr><td class="_artclTdNum">11</td><td class="_artclTdTitle"><a class="artclLinkView" href="/bbs/test/1/11/artclView.do">새 공지</a></td></tr></tbody>',url=url)
        future=MagicMock();future.result.return_value=shell
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp);Path('yesterday.txt').write_text('old\nold\n',encoding='utf-8')
                with patch.object(announcements,'BASE_DIR',Path(temp)),patch.object(announcements,'_get_board_response',side_effect=[shell,normal]) as fetch,patch.object(announcements,'record_board_result') as record,patch.object(announcements,'event'),contextlib.redirect_stdout(io.StringIO()):
                    urls,number=announcements.get_anns_url(page,1,prefetched=future)
                self.assertEqual(number,11)
                self.assertEqual(urls,['https://smartfactory.pusan.ac.kr/bbs/test/1/11/artclView.do'])
                self.assertEqual(fetch.call_args.kwargs,{'isolated':True})
                record.assert_called_once_with(True)
                evidence=list((Path(temp)/'.runtime/board-response-evidence').glob('*.html'))
                self.assertEqual(len(evidence),1)
                self.assertEqual(evidence[0].read_text(encoding='utf-8'),shell.text)
            finally:os.chdir(previous)

    def test_genuinely_missing_list_keeps_saved_number_and_records_failure(self):
        page=types.SimpleNamespace(page_url='https://example.invalid/list',source_url='https://example.invalid/list',department='기관',number=87)
        shell=types.SimpleNamespace(text='<html><main>목록을 가져올 수 없습니다.</main></html>',url=page.page_url)
        with tempfile.TemporaryDirectory() as temp,patch.object(announcements,'BASE_DIR',Path(temp)),patch.object(announcements,'_get_board_response',return_value=shell),patch.object(announcements,'record_board_result') as record,patch.object(announcements,'event'):
            urls,number=announcements.get_anns_url(page,1)
        self.assertEqual((urls,number),([],87))
        record.assert_called_once_with(False)


if __name__=='__main__':unittest.main()
