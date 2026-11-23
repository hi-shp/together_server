import unittest
from together.notices.intelligence.routing import category_hint


class SubmissionAndResults(unittest.TestCase):
    def test_scholarship_selection_schedule_is_not_a_result(self):
        hint = category_hint(
            "2027년도 관정재단 장학생 선발 안내", {},
            body="추천기한: 11월 10일까지 학과사무실. 지원자격: 3학년 진학 예정자. "
                 "추천 장학생 명단과 신청서를 제출합니다. 최종 선발은 12월 예정입니다.",
        )
        self.assertNotEqual(hint.get("method"), "selection_result_body")
        self.assertNotEqual(hint.get("category"), "해당없음")

    def test_scholarship_future_final_selection_alone_is_not_a_result(self):
        hint = category_hint(
            "2027년도 관정재단 장학생 선발 안내", {},
            body="최종 선발: 2027년 1월 예정. 추천 명단 양식을 학과로 제출.",
        )
        self.assertNotEqual(hint.get("method"), "selection_result_body")

    def test_new_scholarship_application_is_not_hidden_by_a_result_reference(self):
        hint = category_hint(
            "장학생 선발 안내", {},
            body="전년도 선발 결과를 참고하세요. 신청기한 11월 12일. 지원자격과 추천대상은 붙임 참조.",
        )
        self.assertNotEqual(hint.get("method"), "selection_result_body")

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
