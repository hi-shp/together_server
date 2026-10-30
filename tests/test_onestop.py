import unittest
from unittest.mock import MagicMock, patch
from together.crawler.onestop import OnestopClient, OnestopError, menu_for, safe_rows


class OnestopTests(unittest.TestCase):
    def test_only_registered_school_menus(self):
        self.assertEqual(menu_for("https://onestop.pusan.ac.kr/page?menuCD=000000000000386"), "000000000000386")
        self.assertIsNone(menu_for("https://evil.example/page?menuCD=000000000000386"))
        self.assertIsNone(menu_for("https://onestop.pusan.ac.kr/page?menuCD=other"))

    def test_personal_and_internal_fields_not_retained(self):
        rows = safe_rows({"data":[{"POSTING_SEQ_NO":4,"TITLE_CONTENT":"안내","EMAIL":"private", "PASSWORD":"secret", "INS_USER_ID":"private", "bbsFileList":[{"ORIGIN_FILE_NM":"guide.pdf", "PARAM_CODE":"signed", "INS_USER_IP":"private"}]}],"pageInfo":{}})
        self.assertNotIn("EMAIL", rows[0])
        self.assertNotIn("PASSWORD", rows[0])
        self.assertNotIn("INS_USER_IP", rows[0]["files"][0])
        with self.assertRaises(OnestopError): safe_rows({"statusCode":403})
        with self.assertRaises(OnestopError):
            safe_rows({"data":[{"POSTING_SEQ_NO":5,"TITLE_CONTENT":"비공개","SECRET_FG":"Y"}],"pageInfo":{}})

    def test_pinned_pages_do_not_hide_new_ordinary_notices(self):
        c=OnestopClient()
        pages = [[{"POSTING_SEQ_NO":10,"NOTICE_FG":"Y"}],
                 [{"POSTING_SEQ_NO":10,"NOTICE_FG":"Y"},{"POSTING_SEQ_NO":12,"NOTICE_FG":"N"}],
                 [{"POSTING_SEQ_NO":8,"NOTICE_FG":"N"}]]
        c.page=lambda code,index:(pages[index],{"totPage":3,"totCnt":3})
        rows,latest,count=c.recent("000000000000386",10)
        self.assertEqual([r["POSTING_SEQ_NO"] for r in rows],[12])
        self.assertEqual((latest,count),(12,3))

    def test_stale_pagination_cannot_advance_cursor(self):
        c=OnestopClient()
        c.page=lambda code,index:([{"POSTING_SEQ_NO":12,"NOTICE_FG":"Y"}],{"totPage":3,"totCnt":3})
        with self.assertRaises(OnestopError):c.recent("000000000000386",10)

    def test_article_preserves_body_images_and_uses_authenticated_attachment_session(self):
        c = OnestopClient(); c.menu = '000000000000386'; c.browser = MagicMock()
        url = 'https://onestop.pusan.ac.kr/page?menuCD=000000000000386&mode=DETAIL&seq=12'
        session = MagicMock(); session.__enter__.return_value = session
        response = session.get.return_value; response.url = url
        response.content = '''<header><img src="logo.png"></header><div class="board-view-title"><h4>교육 안내</h4></div>
        <div class="board-view-cont">본문<img src="/images/poster.png"><script>bad()</script><table><tr><td>내용</td></tr></table></div>
        <div class="board-view-files"><a href="/file/download.do?doc">안내.hwp</a><a href="/file/download.do?img">포스터.jpg</a></div>'''.encode()
        c.attachment_session = lambda: session
        with patch('together.crawler.files.download_attachment', side_effect=[{'path':'draft/안내.hwp','filename':'안내.hwp','bytes':20,'sha256':'a'*64}, None]) as download:
            article = c.article(url, '교육/특강/프로그램')
            self.assertEqual(article.files, ['draft/안내.hwp'])
            self.assertEqual(article.metadata['inline_image_count'], 1)
            self.assertIn('https://onestop.pusan.ac.kr/images/poster.png', article.content_html)
            self.assertIn('<table>', article.content_html)
            self.assertNotIn('<script>', article.content_html)
            self.assertIs(download.call_args.kwargs['http_client'], session)

    def test_failed_authenticated_read_keeps_cursor_and_marks_board_failed(self):
        from together.crawler.onestop import collect
        c = MagicMock(); c.recent.side_effect = OnestopError('incomplete')
        page = MagicMock(page_url='https://onestop.pusan.ac.kr/page?menuCD=000000000000386',number=2216)
        event, result = MagicMock(), MagicMock()
        with patch('together.crawler.onestop.client', return_value=c):
            self.assertEqual(collect(page,2,event,result),([],2216))
        result.assert_called_once_with(False)
        c.close.assert_called_once()
