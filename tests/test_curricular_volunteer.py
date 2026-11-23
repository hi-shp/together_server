import unittest
from together.notices.intelligence.routing import verified_activity_category

class CurricularVolunteerTests(unittest.TestCase):
    def test_teacher_credit_procedure_is_administrative(self):
        r=verified_activity_category('2026학년도 겨울계절수업 교육봉사활동 이수 안내','교직 이수자는 교원자격 취득을 위해 60시간 이상 이수. 상세한 교과목과 학점 인정은 붙임 참조.')
        self.assertEqual(r['category'],'해당없음')

    def test_new_volunteer_recruitment_is_not_suppressed_by_credit_benefits(self):
        r=verified_activity_category('초등학생 교육봉사활동 봉사자 모집','교직 이수자도 신청 가능하며 교육봉사 학점을 인정합니다.')
        self.assertFalse(r and r['category']=='해당없음')

    def test_title_without_curricular_body_does_not_force_an_administrative_label(self):
        r=verified_activity_category('교육봉사활동 이수 안내','')
        self.assertFalse(r and r['method']=='curricular_volunteer_credit')
