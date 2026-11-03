import unittest
from bs4 import BeautifulSoup
from together.notices.intelligence.metadata import extract_metadata

class PublicationHeaderDateTests(unittest.TestCase):
    def metadata(self,html):return extract_metadata(BeautifulSoup(html,'html.parser'),'https://microbio.pusan.ac.kr/detail')
    def test_department_list_label_is_read(self):
        result=self.metadata('<div class="board-view"><div class="title"><ul class="detail"><li><span>작성일</span>2026.11.03</li></ul></div><div class="artclView">행사일2026.12.01</div></div>')
        self.assertEqual(result['publication_time'],'2026-11-03')
        self.assertEqual(result['publication_time_source'],'explicit_article_header_label')
    def test_university_creation_date_is_not_expiration_date(self):
        result=self.metadata('<div class="vtitle-winfo"><span class="txt">작성일자 2026-11-03</span><span class="txt">게시종료일 2026-11-17</span></div>')
        self.assertEqual(result['publication_time'],'2026-11-03')
    def test_only_expiration_or_body_date_is_not_a_publication_date(self):
        result=self.metadata('<div class="vtitle-winfo"><span class="txt">게시종료일 2026-11-17</span></div><div class="artclView"><li>작성일2026.11.04</li></div>')
        self.assertIsNone(result['publication_time'])
    def test_invalid_calendar_date_is_not_accepted(self):
        result=self.metadata('<div class="vtitle-winfo"><span class="txt">작성일자 2026-02-30</span></div>')
        self.assertFalse(result['publication_time_is_explicit'])

if __name__=='__main__':unittest.main()
