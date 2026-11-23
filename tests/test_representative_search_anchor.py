import unittest
from together.notices.intelligence.representative import literal_search_anchor

class LiteralSearchAnchorTests(unittest.TestCase):
    def test_event_word_is_used_instead_of_year_or_department_prefix(self):
        self.assertEqual(literal_search_anchor('[학생지원시스템] [대학공지] 2026학년도 2학기 대학원생 핵심역량진단 실시 안내'), '핵심역량진단')

    def test_anchor_is_a_literal_word_from_the_title(self):
        title = '[박물관] 2026학년도 추계답사 참가 신청 안내'
        anchor = literal_search_anchor(title)
        self.assertEqual(anchor, '추계답사')
        self.assertIn(anchor, title)

    def test_short_generic_titles_still_have_a_searchable_fallback(self):
        self.assertEqual(literal_search_anchor('[공지사항] 안내'), '안내')
