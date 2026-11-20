import unittest
from together.notices.intelligence.routing import verified_activity_category


class VerifiedActivityTests(unittest.TestCase):
    def test_actual_activity_overrides_topic_words(self):
        cases = [
            ('학술지 미래기술문화융합논문지 발간 사업', '논문 투고 후 심사 게재. 편당 연구비 지급', '해당없음'),
            ('커리어로드맵 공모전 학생심사단 모집', '우수작 선정, 심사기준과 심사대상 안내', '서포터즈'),
            ('계약직원 최종 합격자 공고', '공개채용 최종 합격자 공고', '해당없음'),
            ('교직 인성검사 고사장 및 수험생 안내', '수험표와 신분증을 지참하고 입실', '해당없음'),
            ('영재교육대상자 선발 2차전형 진행 안내', '1차 합격자 수험표를 출력하고 입실', '해당없음'),
            ('총장배 배드민턴대회 개최 안내', '참가대상: 학생, 교직원. 신청기한 11월24일', '교육/특강/프로그램'),
        ]
        for title, body, category in cases:
            with self.subTest(title=title):
                self.assertEqual(verified_activity_category(title, body)['category'], category)

    def test_different_activities_are_not_forced(self):
        cases = [
            ('학술지 우수논문상 공모전', '논문투고, 상금과 시상'),
            ('직원 합격자 공고 및 추가채용', '합격자 안내 및 추가 지원자 모집'),
            ('배드민턴대회 결과 안내', '참가대상 및 신청기한이 있었으며 수상 결과 안내'),
            ('영재교육 신규 수강생 모집', '지원 접수 방법과 수험표'),
            ('공모전 참가팀 모집', '심사기준과 심사대상'),
        ]
        for title, body in cases:
            with self.subTest(title=title):
                self.assertIsNone(verified_activity_category(title, body))
