import unittest
from unittest.mock import patch
from together.operations.events import decision_explanation
from together.notices import history


class DecisionLogDetail(unittest.TestCase):
    def test_reason_is_single_line_and_sensitive_values_are_redacted(self):
        s = decision_explanation('GPT 본문 검토', '학교가 다름\n학생 마감도 다름 test@example.com')
        self.assertNotIn('\n', s)
        self.assertIn('학교가 다름 학생 마감도 다름', s)
        self.assertNotIn('test@example.com', s)

    def test_scholarship_allowed_log_and_no_model_or_representative_lookup(self):
        with patch.object(history, 'event') as event, patch.object(history, 'check_title_similarity') as api:
            self.assertEqual(history.is_recent_title_duplicate('푸른등대 장학생 신청', representative_check=lambda _: self.fail('must not suppress scholarship')), '중복 아님')
        api.assert_not_called()
        self.assertIn('중복 체크 결과: 중복 허용 (장학금,', event.call_args.args[1])
        self.assertEqual(event.call_args.kwargs['result'], '중복 허용')

    def test_non_scholarship_keeps_duplicate_decision(self):
        with patch.object(history, 'event') as event:
            self.assertEqual(history.is_recent_title_duplicate('독립된 신규 교육 행사', filename='/nonexistent-test-title-history'), '중복 아님')
        self.assertIn('최근 비교 범위', event.call_args.args[1])
        self.assertNotIn('중복 허용', event.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
