import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from together.storage.pending_notices import PendingNotices
from together.crawler import announcements
from together import application
from together.notices.intelligence.publication import remember_receipt


BOARD = 'https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?mgr_seq=3&page=1'
SOURCE = BOARD + '&mode=view&board_seq=123456'


class PendingRecovery(unittest.TestCase):
    def test_pinned_failure_survives_legacy_title_advancement(self):
        html = '<tbody><tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?mgr_seq=3&page=1&mode=view&board_seq=123456">새 고정 공지</a></td></tr><tr><td class="num">195</td></tr></tbody>'
        page = types.SimpleNamespace(page_url=BOARD, source_url=BOARD, department='대학 공지', number=195)
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            try:
                os.chdir(root)
                Path('yesterday.txt').write_text('ordinary\n이전 고정 공지\n', encoding='utf-8')
                journal = PendingNotices(root)
                seen = []
                def discovered(url):
                    seen.append(Path('yesterday.txt').read_text(encoding='utf-8'))
                    journal.add(BOARD, url, page.department)
                page.on_discovered = discovered
                with patch.object(announcements, '_get_board_response', return_value=types.SimpleNamespace(text=html, url=BOARD)), patch.object(announcements, 'recent_title', return_value=[]), patch.object(announcements, 'event'), patch.object(announcements, 'record_board_result'):
                    first, number = announcements.get_anns_url(page, 1)
                    self.assertEqual(len(first), 1)
                    self.assertIn('이전 고정 공지', seen[0])
                    self.assertIn('새 고정 공지', Path('yesterday.txt').read_text(encoding='utf-8'))
                    # Simulate an API failure after collection and process restart.
                    recovered = PendingNotices(root)
                    second, number = announcements.get_anns_url(page, 1)
                    self.assertEqual(second, [])
                    self.assertEqual(recovered.merge(recovered.for_board(BOARD), second), first)
                    self.assertEqual(number, 195)
            finally:
                os.chdir(old_cwd)

    def test_original_url_is_preserved_across_pagination_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = PendingNotices(temp)
            journal.add(BOARD, SOURCE, '대학 공지')
            other = SOURCE.replace('page=1', 'page=2')
            journal.add(BOARD, other, '대학 공지')
            self.assertEqual(journal.merge(journal.for_board(BOARD), [other]), [SOURCE])
            self.assertEqual(len(journal.for_board(BOARD)), 1)

    def test_other_completed_board_is_not_rolled_back(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = PendingNotices(temp)
            journal.add(BOARD, SOURCE, '대학 공지')
            journal.finish(BOARD, SOURCE)
            journal.add('later', 'https://later.pusan.ac.kr/post', '다른 학과')
            self.assertEqual(PendingNotices(temp).for_board(BOARD), [])
            self.assertEqual(len(journal.for_board('later')), 1)

    def test_corrupt_journal_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = PendingNotices(temp)
            journal.path.write_text('{invalid', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                journal.for_board(BOARD)
            self.assertEqual(journal.path.read_text(), '{invalid')

    def test_notification_failure_resumes_despite_saved_university_title(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            try:
                os.chdir(root)
                (root / 'downloads').mkdir()
                (root / 'titles.txt').write_text('', encoding='utf-8')
                (root / 'sorted_labeled_data.csv').write_text('', encoding='utf-8')
                (root / 'pages.csv').write_text('department,page_url,number\n대학 공지,' + BOARD + ',195\n', encoding='utf-8')
                page = types.SimpleNamespace(page_url=BOARD, source_url=BOARD, department='대학 공지', number=195)
                manager = types.SimpleNamespace(announcement_pages=[page])
                receipt = {'source_url':SOURCE,'article_url':'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2','subject':'새 고정 공지','state':'verified'}
                service = MagicMock()
                posts = [];messages = set();emails = set();fail = [True]
                def post(*args):
                    posts.append(SOURCE);remember_receipt(root, receipt);return [receipt]
                def message(*args, **kw):messages.add(SOURCE)
                def email(*args, **kw):
                    if fail[0]:fail[0]=False;raise RuntimeError('Email queue acknowledgement unavailable')
                    emails.add(SOURCE)
                service.write_notices.side_effect = post
                service.send_message.side_effect = message
                service.send_email.side_effect = email
                service.update_participants.return_value = 10
                service.download_survey.return_value = 2
                def ann(*args):return types.SimpleNamespace(title='새 고정 공지',original_title='새 고정 공지',url=SOURCE,content_text='교육 안내',content_html='<p>교육 안내</p>',files=[],metadata={})
                patches = {'BASE_DIR':root,'PageUrlManager':lambda:manager,'OrderedBoardPrefetch':lambda pages:types.SimpleNamespace(take=lambda n:None,close=lambda:None),'get_anns_url':MagicMock(side_effect=[([SOURCE],196),([],195)]),'crawl_ann_partial':ann,'fetch_complete_notice':ann,'find_target':lambda *a:None,'is_recent_title_duplicate':lambda *a,**k:'중복' if messages else '중복 아님','categorize_announcement':lambda *a:'교육/특강/프로그램','notify':lambda *a,**k:None,'event':lambda *a,**k:None,'observe_notice':lambda *a:None,'archive_files':lambda *a:None,'register_post':lambda *a:None,'update_state':lambda *a,**k:None,'update_csv_with_announcement_numbers':MagicMock()}
                with patch.dict(os.environ,{'PAGE_NAME':'pages.csv','POST_VALIDATION_MODE':'off'}), patch('os.path.expanduser',return_value=str(root)), contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
                    for name,value in patches.items():stack.enter_context(patch.object(application,name,value))
                    prepared=('2026-11-03',types.SimpleNamespace(update_university_notices=lambda:None),manager)
                    with self.assertRaises(RuntimeError):application.run_cycle(service,prepared)
                    self.assertTrue(PendingNotices(root).notifications_started(BOARD,SOURCE))
                    self.assertIn('새 고정 공지',(root/'titles.txt').read_text(encoding='utf-8'))
                    application.run_cycle(service,prepared)
                self.assertEqual(posts,[SOURCE])
                self.assertEqual(len(messages),1)
                self.assertEqual(len(emails),1)
                self.assertEqual(PendingNotices(root).for_board(BOARD),[])
                self.assertEqual(patches['update_csv_with_announcement_numbers'].call_count,0)
            finally:
                os.chdir(old_cwd)


    def test_missing_source_keeps_pending_and_allows_later_board(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            try:
                os.chdir(root)
                (root/'downloads').mkdir()
                (root/'titles.txt').write_text('',encoding='utf-8')
                (root/'sorted_labeled_data.csv').write_text('',encoding='utf-8')
                first=types.SimpleNamespace(page_url=BOARD,source_url=BOARD,department='첫 학과',number=10)
                second=types.SimpleNamespace(page_url='https://later.pusan.ac.kr/list',source_url='https://later.pusan.ac.kr/list',department='다음 학과',number=20)
                manager=types.SimpleNamespace(announcement_pages=[first,second]);service=MagicMock()
                fetch=MagicMock(side_effect=[([SOURCE],11),([],20)])
                partial=types.SimpleNamespace(title='제목 없음',content_text='내용 없음')
                patches={'BASE_DIR':root,'PageUrlManager':lambda:manager,'OrderedBoardPrefetch':lambda pages:types.SimpleNamespace(take=lambda n:None,close=lambda:None),'get_anns_url':fetch,'crawl_ann_partial':lambda *a:partial,'notify':lambda *a,**k:None,'event':lambda *a,**k:None,'update_state':lambda *a,**k:None,'update_csv_with_announcement_numbers':MagicMock()}
                with patch.dict(os.environ,{'PAGE_NAME':'pages.csv'}),patch('os.path.expanduser',return_value=str(root)),patch('time.sleep'),patch('together.operations.state.record_detail_failure') as detail_failed,contextlib.ExitStack() as stack,contextlib.redirect_stdout(io.StringIO()):
                    for name,value in patches.items():stack.enter_context(patch.object(application,name,value))
                    with patch('together.crawler.detail_recovery.probe',return_value={'reason':'school_access_message'}):
                        application.run_cycle(service,('2026-11-04',types.SimpleNamespace(update_university_notices=lambda:None),manager))
                self.assertEqual(fetch.call_count,2)
                patches['update_csv_with_announcement_numbers'].assert_not_called()
                service.write_notices.assert_not_called();service.send_message.assert_not_called();service.send_email.assert_not_called()
                detail_failed.assert_called_once()
                pending=PendingNotices(root).for_board(BOARD)
                self.assertEqual(len(pending),1);self.assertEqual(pending[0]['source_review'],'article_unreadable')
                self.assertFalse(pending[0]['notifications_started'])
            finally:os.chdir(old_cwd)


if __name__ == '__main__':unittest.main()
