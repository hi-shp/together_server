import unittest
from together.notices.intelligence.routing import category_hint


class SubmissionAndResults(unittest.TestCase):
    def test_selected_students_followup_is_not_an_open_scholarship(self):
        hint = category_hint(
            "[장학] 2026년 문헌정보학과 동문 장학생 선정 공고",
            {},
            body="선발인원: 4명. 장학생 명단: 개별 통보. 추가 제출서류: 성적증명서, 재학증명서.",
        )
        self.assertEqual(hint["category"], "해당없음")

    def test_open_scholarship_selection_still_needs_its_normal_route(self):
        hint = category_hint(
            "장학생 선정 공고",
            {},
            body="선발인원 4명. 신청기간 10/20~10/30. 지원방법: 신청서 제출.",
        )
        self.assertIsNone(hint["category"])

    def test_data_theme_with_visual_and_short_video_sections_is_visual(self):
        hint = category_hint(
            "사회과학데이터 해커톤 공모전 안내",
            {},
            body="공모내용: 데이터로 발견하는 도시와 캠퍼스. 팀 단위 참가. 시상내역: 시각화, 솟폼 부분별 최우수상 각 1팀.",
        )
        self.assertEqual(hint["category"], "[공모전] 미술/디자인/건축")

    def test_software_implementation_is_not_overridden_by_a_demo_video(self):
        hint = category_hint(
            "사회과학데이터 해커톤 공모전",
            {},
            body="프로그램 구현과 소스코드 제출이 필수이며 시연 영상 제작을 권장합니다.",
        )
        self.assertIsNone(hint["category"])

    def test_reference_poster_is_not_an_artifact_requirement(self):
        hint = category_hint(
            "데이터 해커톤 공모전",
            {},
            body="신청은 첨부 포스터 참고. 시각화 기법을 활용할 수 있습니다.",
        )
        self.assertIsNone(hint["category"])
