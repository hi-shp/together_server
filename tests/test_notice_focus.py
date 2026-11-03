import unittest
from together.notices.intelligence.focus import exchange_focus,focus_conflict,dispatch_evidence,focus_text


class NoticeFocusTests(unittest.TestCase):
    def test_aliases_share_family_but_other_school_is_distinct(self):
        first='2026학년도 겨울계절수업 교류 수학 안내(서울시립대학교)'
        second='[학사과] 서울시립대 2026 동계 학점교류 안내'
        self.assertEqual(exchange_focus(first)['occurrence_key'],exchange_focus(second)['occurrence_key'])
        self.assertEqual(focus_conflict(first,first.replace('서울시립대학교','서강대학교')),'exchange_target_schools_conflict')
        self.assertEqual(exchange_focus(first)['target_schools'],['서울시립대학교'])

    def test_annual_memory_never_erases_a_new_occurrence(self):
        a=exchange_focus('2026학년도 겨울계절수업 교류수학(서강대학교)')
        b=exchange_focus('2027학년도 겨울계절수업 교류수학(서강대학교)')
        self.assertEqual(a['family_memory_key'],b['family_memory_key'])
        self.assertNotEqual(a['occurrence_key'],b['occurrence_key'])
        self.assertEqual(focus_conflict('2026 동계 교류수학(서강대학교)','2027 동계 교류수학(서강대학교)'),'exchange_years_conflict')

    def test_specific_program_is_preserved(self):
        self.assertNotEqual(exchange_focus('2026 동계 교류수학(한양대학교 에너지신산업)')['occurrence_key'],exchange_focus('2026 동계 교류수학(한양대학교 국제동계대학)')['occurrence_key'])

    def test_missing_school_or_term_is_not_complete(self):
        self.assertFalse(exchange_focus('2026학년도 교류수학 안내')['occurrence_complete'])
        self.assertFalse(exchange_focus('2026 동계 교류수학 안내','서울대학교 안내를 참고하세요')['occurrence_complete'])

    def test_partial_term_or_joint_school_overlap_is_not_a_conflict(self):
        self.assertIsNone(focus_conflict('2026학년도 2학기 동계 서울시립대 교류수학','2026 동계 서울시립대 교류수학'))
        self.assertIsNone(focus_conflict('2026 동계 서울대·서강대 교류수학','2026 동계 서강대 교류수학'))

    def test_observed_time_is_not_publication_time(self):
        a={'observed_at':'2026-11-03T12:00:00'}
        self.assertFalse(dispatch_evidence(a,a)['explicit_publication_dates'])
        self.assertIsNone(dispatch_evidence(a,a)['days_apart'])

    def test_real_original_dates_are_secondary_evidence(self):
        a={'publication_time':'2026-10-20','publication_time_is_explicit':True}
        b={'publication_time':'2026-11-02','publication_time_is_explicit':True}
        result=dispatch_evidence(a,b)
        self.assertEqual(result['days_apart'],13)
        self.assertTrue(result['within_fourteen_days'])
        self.assertFalse(result['within_seven_days'])
        self.assertFalse(result['alone_can_confirm_duplicate'])

    def test_focus_input_keeps_the_original_material(self):
        text=focus_text('2026 동계 서울시립대학교 교류수학','학과 제출 기한은 10월 30일입니다.')
        self.assertIn('대상 대학: 서울시립대학교',text)
        self.assertIn('10월 30일',text)

    def test_close_dates_never_override_different_target_schools(self):
        a={'publication_time':'2026-10-20','publication_time_is_explicit':True}
        self.assertTrue(dispatch_evidence(a,a)['within_two_days'])
        self.assertEqual(focus_conflict('2026 동계 서울대 교류수학','2026 동계 서강대 교류수학'),'exchange_target_schools_conflict')


if __name__=='__main__':unittest.main()
