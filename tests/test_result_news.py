"""Result publicity stays in miscellaneous notices across decision providers."""
import os
import unittest
from unittest.mock import patch
from together.notices.intelligence.routing import category_hint, verified_activity_category


class ResultNewsTests(unittest.TestCase):
    def test_awards_results_and_completed_event_publicity_are_miscellaneous(self):
        cases = [
            ('22학번 학생 "2026 캠퍼스 특허유니버시아드 대회 한국중견기업연합회장상 수상"', '학생팀이 한국중견기업연합회장상을 수상했고 시상식은 11월 19일 개최됐다.'),
            ('학생팀 전국 대학생 경진대회 우수상 수상', ''),
            ('공모전 수상 소식', '올해 대회에 참가한 학생들의 수상 성과를 전합니다.'),
            ('2026 공모전 수상자 발표', '수상자 명단과 심사 결과 안내'),
            ('영상 콘텐츠 공모전 결과 발표', '접수가 마감되어 선정 작품을 공지합니다.'),
            ('배드민턴대회 결과 안내', '참가대상과 신청기한이 있었으며 최종 결과 안내'),
            ('전국 대회 입상 안내', ''),
            ('학과 학생팀 대회 수상자 인터뷰', '지난 대회에서 대상을 수상했다.'),
            ('창업 캠프 성료', '참가 학생들이 교육을 마쳤다.'),
            ('인문학 특강 개최 결과', '지난 특강을 마무리했다.'),
            ('지역 축제 성황리 개최', '지난 주 축제를 성황리에 마쳤다.'),
            ('우리 학과 소식', '학생팀이 전국 경진대회에서 우수상을 수상했다.'),
        ]
        for title, body in cases:
            with self.subTest(title=title):
                result = verified_activity_category(title, body)
                self.assertIsNotNone(result)
                self.assertEqual(result['category'], '해당없음')
                self.assertEqual(category_hint(title, {'공모전': '[공모전] 기타'}, body=body)['category'], '해당없음')

    def test_open_participation_and_prior_award_background_keep_their_activity(self):
        cases = [
            ('2026 캠퍼스 특허유니버시아드 대회 참가팀 모집', '지난해 학생들이 우수상을 수상했다. 신청기간은 12월 1일부터이며 참가 신청서를 제출한다.'),
            ('2026 아이디어 공모전 개최 안내', '작년 수상자와 수상작을 소개합니다. 올해는 신규 아이디어를 접수합니다.'),
            ('공모전 수상 전략 특강 안내', '누구나 신청할 수 있는 특강'),
            ('공모전 수상자 초청 강연 참가 신청', '신청 링크와 강연 일정'),
            ('수상작 전시회 관람 안내', '학생 누구나 관람 가능'),
            ('대회 결과 발표회 참가자 모집', '발표회 참가 신청을 받습니다.'),
            ('2027 장학생 선발 및 신청 안내', '지난해 선발 결과와 새 신청 방법'),
            ('수상 소감 글쓰기 공모전', '수상 경험을 주제로 수기를 응모하세요.'),
            ('공모전 학생 투표 참여 안내', '현재 투표 링크와 참여 방법'),
            ('수상스포츠 체험 캠프 참가 신청', '수상 안전 교육과 카약 체험'),
        ]
        for title, body in cases:
            with self.subTest(title=title):
                result = verified_activity_category(title, body)
                self.assertFalse(result and result.get('method') == 'result_news')

    def test_result_policy_overrides_a_wrong_remote_category_in_both_modes(self):
        from together import application
        title = '학생팀 2026 캠퍼스 특허유니버시아드 대회 우수상 수상'
        for mode in ('hybrid', 'legacy'):
            with (
                self.subTest(mode=mode),
                patch.dict(os.environ, {'NOTICE_CLASSIFIER_MODE': mode}),
                patch.object(application, 'configured_classifier', return_value=None),
                patch.object(application, 'answer_gpt', return_value='[공모전] 공학/IT/SW'),
                patch.object(application, 'review_for', return_value={}),
                patch.object(application, 'observe_category'),
                patch.object(application, 'event'),
                patch('together.learning.campaign.enabled', return_value=True),
            ):
                self.assertEqual(application.categorize_announcement(title, '학생팀이 대회에서 우수상을 수상했다.'), '해당없음')
