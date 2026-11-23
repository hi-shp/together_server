from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from together.notices.intelligence.focus import focus_conflict
from together.notices import history

TITLE = '부산대학교 연구처 연구안보센터 계약직원 공개채용 공고'
OLD = '계약직원을 공개 모집합니다. 2026. 11. 3. 부산대학교 연구처장'
NEW = '계약직원을 공개 모집합니다. 2026. 11. 17. 부산대학교 연구처장'

class HiringIssueDateTests(unittest.TestCase):
    def test_different_explicit_positions_keep_the_first_notice_for_each(self):
        left = '부산대학교 AX·정보화혁신본부 계약직원(전임연구원, 사무원) 공개채용 공고'
        right = '부산대학교 AX·정보화혁신본부 계약직원(연구보조원) 공개채용 공고'
        self.assertEqual(focus_conflict(left, right), 'hiring_position_conflict')

    def test_a_partial_title_with_an_overlapping_position_is_not_a_conflict(self):
        left = '부산대학교 AX·정보화혁신본부 계약직원(전임연구원, 사무원) 공개채용 공고'
        right = '부산대학교 AX·정보화혁신본부 사무원 공개채용 공고'
        self.assertIsNone(focus_conflict(left, right))

    def test_same_employer_different_signed_dates_preserve_both_rounds(self):
        self.assertEqual(focus_conflict(TITLE, TITLE, NEW, OLD), 'hiring_official_issue_date_conflict')

    def test_forwarding_posting_dates_do_not_replace_the_official_date(self):
        self.assertIsNone(focus_conflict(TITLE, TITLE, OLD + ' 학과 게시일 2026. 11. 17.', OLD))

    def test_same_title_fast_path_cannot_skip_the_body_round_check(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'titles.txt'
            path.write_text(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '$' + TITLE + '\n', encoding='utf8')
            context = {'new_notice': {'body_excerpt': NEW}, 'recent_notices': [{'title': TITLE, 'body_excerpt': OLD}]}
            with patch.object(history, 'duplicate_context', return_value=context), patch.object(history, 'compare_notices', return_value={'relation': 'same_event'}), patch.object(history, 'check_title_similarity', return_value='중복 아님') as review, patch.object(history, 'event'):
                result = history.is_recent_title_duplicate(TITLE, str(path), announcement=SimpleNamespace(title=TITLE, content_text=NEW, url='https://school.invalid/new-round'))
            self.assertEqual(result, '중복 아님')
            review.assert_called_once()
