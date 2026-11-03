import unittest
from together.learning.inputs import keyword_signals,category_input,pair_input

class LearningInputTests(unittest.TestCase):
    def test_all_keywords_are_features_instead_of_first_match_truth(self):
        signals=keyword_signals('장학 관련 특강 모집','',{'장학':'장학금','특강':'교육/특강/프로그램'},[])
        self.assertEqual(len(signals['title_hits']),2)
        self.assertFalse(signals['forces_category'])
        self.assertEqual(signals['legacy_first_match_proposal'],'장학금')
    def test_result_word_and_body_survive_a_scholarship_keyword(self):
        value=category_input('장학생 선발 결과 안내','신규 신청을 받지 않습니다.',keyword_categories={'장학':'장학금'},exclude_keywords=['결과'])
        self.assertIn('정답 아님',value)
        self.assertIn('결과',value)
        self.assertIn('신규 신청을 받지 않습니다.',value)
    def test_target_school_is_kept_in_cross_attention_inputs(self):
        left,right=pair_input({'title':'2026 동계 서울시립대 교류수학','body':'학과 신청 조건 A'},{'title':'2026 동계 서강대 교류수학','body':'학과 신청 조건 B'})
        self.assertIn('대상 대학: 서울시립대학교',left)
        self.assertIn('대상 대학: 서강대학교',right)
        self.assertIn('조건 A',left);self.assertIn('조건 B',right)
    def test_collection_time_is_not_promoted_to_a_publication_feature(self):
        values=pair_input({'title':'2026 캠프','observed_at':'2026-11-03'},{'title':'2026 캠프','observed_at':'2026-11-03'})
        self.assertNotIn('0일 차이',values[0])

if __name__=='__main__':unittest.main()
