import unittest
from together.notices.intelligence.routing import category_hint


class RoleClassification(unittest.TestCase):
    def test_actual_recruitment_roles(self):
        cases = [
            (
                "2026학년도 제39기 부산대학교 산학협력 해외봉사단 참가자 추가 모집 안내(기간연장)",
                "봉사활동",
            ),
            (
                "2026학년도 겨울계절학기 현장실습학기제 실습기관, 학생 모집 및 수강신청 안내",
                "취업 정보",
            ),
            (
                "2026학년도 겨울계절학기 현장실습학기제 학생 모집 및 수강신청 안내",
                "취업 정보",
            ),
            (
                "[학위논문 연구 참여자 모집공고문] (설문 응답시 기프티콘 제공)",
                "해당없음",
            ),
            (
                "부산대-부산교대 통합을 위한 정보서비스 디자인 시안 의견 수렴 안내",
                "해당없음",
            ),
        ]
        for title, wanted in cases:
            with self.subTest(title=title):
                self.assertEqual(category_hint(title, {})["category"], wanted)

    def test_paid_exam_staff_uses_actual_pay_terms(self):
        title = "2027학년도 논술고사 진행요원 모집 안내"
        self.assertEqual(
            category_hint(title, {}, body="근무시간에 따라 최저시급 지급")["category"],
            "취업 정보",
        )
        self.assertIsNone(
            category_hint(title, {}, body="수당 없는 무급 봉사")["category"]
        )

    def test_education_participation_demand_is_not_a_feedback_survey(self):
        self.assertIsNone(
            category_hint("위성망 국제등록 교육 참여 수요조사", {})["category"]
        )

    def test_volunteer_results_are_still_administrative(self):
        self.assertEqual(
            category_hint("해외봉사단 참가자 모집 선발 결과 공고", {})["category"],
            "해당없음",
        )
