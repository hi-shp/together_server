import unittest
from together.notices.intelligence.routing import verified_activity_category


class VerifiedActivityTests(unittest.TestCase):
    def test_technical_competition_is_not_only_a_coding_lecture(self):
        title = '공과대학 AX 아이디어 경진대회 홍보 협조 요청'
        body = 'PNU ECO-X AI 코딩캠프. 프로젝트 예시: 수질 예측. 딥러닝 접근 예시: LSTM'
        self.assertEqual(verified_activity_category(title, body)['category'], '[공모전] 공학/IT/SW')
        self.assertIsNone(verified_activity_category('AI 코딩캠프 교육생 모집', body))
        self.assertIsNone(verified_activity_category(title, '아이디어 제안서와 사업 기획을 평가합니다'))

    def test_major_competency_program_with_actual_volunteer_duties(self):
        title = '2026학년도 동계 너의 재능이 보여 추가 모집 공고'
        body = '예술 분야 전공역량 강화. 활동 기관에서 5일, 30시간 이상 봉사활동 진행'
        self.assertEqual(verified_activity_category(title, body)['category'], '봉사활동')
        self.assertIsNone(verified_activity_category(title, '전공 실기교육 및 작품 제작 프로그램 참여 안내'))
        self.assertIsNone(verified_activity_category('장학생 모집', '신청자격: 30시간 이상 봉사활동 진행자'))

    def test_language_exam_prize_does_not_bypass_duplicate_review(self):
        from together.notices.intelligence.policy import scholarship_duplicate_allowed
        self.assertFalse(scholarship_duplicate_allowed('2026 하반기 모의 시험(토익, 토플) 경시대회 안내(장학금)'))
        self.assertTrue(scholarship_duplicate_allowed('2027년 1학기 국가장학금 신청 안내'))
        self.assertTrue(scholarship_duplicate_allowed('장학생 추천 및 학과별 신청 안내'))
    def test_actual_activity_overrides_topic_words(self):
        cases = [
            ('겨울 현장실습학기제 학생 모집', '기업부담 실습지원비 168만원. 직무수행 75%', '취업 정보'),
            ('연구기관 동계 현장실습 프로그램 운영 안내', '실습비 1,680,000원 / 월 지급 및 산재보험 가입', '취업 정보'),
            ('슬로건 공모전 2차 학생 투표 일정 변경 안내', '412건의 작품이 접수되었습니다. 투표 시작 시 방법 별도 안내', '해당없음'),
            ('교수님 PASS 상담 안내', '미신청 또는 미상담시 성적열람 불가', '해당없음'),
            ('마이크로디그리 취득 안내', '해당 학기 취득자에 한해 적용하는 이수교과목', '해당없음'),
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
            ('병원 동계 서브인턴 모집', '의대생에게 임상실습 기회를 제공하는 교육'),
            ('해외 현장실습 프로젝트 참여학생 모집', '장학금으로 현지 교육비와 숙박비 지원'),
            ('공모전 학생 투표 참여 안내', '투표 방법과 경품, 현재 투표 링크 안내'),
            ('마이크로디그리 프로그램 설명회 참가 신청', '이수교과목 및 이수학점을 설명합니다'),
            ('취업 PASS 상담 프로그램 모집', '참여를 희망하는 학생 누구나 신청'),
            ('학술지 우수논문상 공모전', '논문투고, 상금과 시상'),
            ('직원 합격자 공고 및 추가채용', '합격자 안내 및 추가 지원자 모집'),
            ('영재교육 신규 수강생 모집', '지원 접수 방법과 수험표'),
            ('공모전 참가팀 모집', '심사기준과 심사대상'),
        ]
        for title, body in cases:
            with self.subTest(title=title):
                self.assertIsNone(verified_activity_category(title, body))
