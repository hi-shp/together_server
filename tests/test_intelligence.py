"""분류 계약, 회차 보호, 메타데이터와 별도 기록의 안전성을 검사한다."""
from datetime import datetime, timedelta
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ['NOTICE_INTELLIGENCE_MODE'] = 'off'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules['gpt_client'] = types.SimpleNamespace(answer_gpt=MagicMock(), check_title_similarity=MagicMock())

from bs4 import BeautifulSoup
from notice_intelligence.text import identity, occurrence_conflict, clean_display_title, NORMALIZATION_VERSION
from notice_intelligence.events import Notice, compare_notices, group_candidates, select_representative
from notice_intelligence.metadata import extract_metadata
from notice_intelligence.model import TitleClassifier
from notice_intelligence.routing import category_hint
from notice_intelligence.shadow import ShadowRecorder, observe_notice
import duplicate_checker
import main


class IdentityTests(unittest.TestCase):
    def test_year_round_semester_and_season_are_preserved(self):
        result = identity('【2026학년도】 제12회 해양 공모전 3기 2차 1학기 하계 모집')
        self.assertEqual(result['years'], ['2026'])
        self.assertEqual(set(result['rounds']), {'12회', '3기', '2차'})
        self.assertEqual(result['semesters'], ['1'])
        self.assertEqual(result['seasons'], ['summer'])

    def test_nested_deadline_is_removed_but_round_is_kept(self):
        title = clean_display_title('⭐ [공지] 2026년 해양 창작 캠프(2차) 모집(~8.25.(월) 17시까지)')
        self.assertIn('(2차)', title)
        self.assertNotIn('8.25', title)
        self.assertIn('2026', title)

    def test_different_year_and_round_are_conflicts(self):
        self.assertEqual(occurrence_conflict('2025년 제3기 해양캠프', '2026년 제3기 해양캠프'), 'years')
        self.assertEqual(occurrence_conflict('2026 제2회 강연', '2026 제4회 강연'), 'round_회')
        self.assertIsNone(occurrence_conflict('2026 하계 캠프', '2026 여름학기 캠프'))

    def test_round_without_spaces_and_leading_zero(self):
        self.assertEqual(occurrence_conflict('제3기모집', '제4기모집'), 'round_기')
        self.assertIsNone(occurrence_conflict('제03기 모집', '제3기 모집'))


class EventTests(unittest.TestCase):
    def notice(self, title, **kwargs):
        return Notice(title, observed_at='2026-10-26T12:00:00', **kwargs)

    def test_source_url_does_not_override_a_different_year(self):
        a = self.notice('2025년 제1회 바다 로봇 경진대회', source_url='https://example.invalid/1')
        b = self.notice('2026년 제1회 바다 로봇 경진대회', source_url='https://example.invalid/1')
        self.assertEqual(compare_notices(a, b)['relation'], 'different_occurrence')

    def test_same_event_can_cross_departments(self):
        a = self.notice('[학부] 2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집', department='조선해양')
        b = self.notice('2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집', department='기계')
        self.assertEqual(compare_notices(a, b)['relation'], 'same_event')

    def test_result_and_recruitment_are_not_silently_merged(self):
        a = self.notice('2026년 해양 로봇 경진대회 참가팀 모집')
        b = self.notice('2026년 해양 로봇 경진대회 선발 결과 공고')
        self.assertNotEqual(compare_notices(a, b)['relation'], 'same_event')

    def test_employer_scope_requires_evidence(self):
        a = self.notice('[현대카드|현대커머셜] 2026년 상반기 Internship 모집(~3/16)')
        b = self.notice('[현대카드] 2026년 상반기 Internship 모집(~3/16)')
        self.assertEqual(compare_notices(a, b)['relation'], 'needs_review')

    def test_deadline_change_is_not_discarded(self):
        a = self.notice('2026년 제3기 해양 로봇 개발 캠프 모집(~10/20)')
        b = self.notice('2026년 제3기 해양 로봇 개발 캠프 모집(~10/30)')
        self.assertEqual(compare_notices(a, b)['relation'], 'related_update')

    def test_department_specific_notices_are_not_merged(self):
        a = self.notice('2026학년도 2학기 졸업논문 제출 안내', department='기계')
        b = self.notice('2026학년도 2학기 졸업논문 제출 안내', department='화학')
        self.assertEqual(compare_notices(a, b)['relation'], 'needs_review')

    def test_missing_department_cannot_confirm_scholarship_scope(self):
        a = self.notice('2026학년도 2학기 근로장학생 선발 안내')
        self.assertEqual(compare_notices(a, a)['relation'], 'needs_review')

    def test_missing_publisher_cannot_confirm_generic_recruitment(self):
        a = self.notice('[현대카드] 2026년 상반기 대학생 인턴십 프로그램 모집')
        b = self.notice('2026년 상반기 대학생 인턴십 프로그램 모집')
        self.assertEqual(compare_notices(a, b)['relation'], 'needs_review')

    def test_same_url_cannot_hide_updated_deadline(self):
        a = self.notice('2026년 제3기 해양 로봇 개발 캠프 모집(~10/20)', source_url='https://example.invalid/1')
        b = self.notice('2026년 제3기 해양 로봇 개발 캠프 모집(~10/30)', source_url='https://example.invalid/1')
        self.assertEqual(compare_notices(a, b)['relation'], 'related_update')

    def test_similar_title_chain_does_not_merge_incompatible_members(self):
        notices = [self.notice(title) for title in ('A', 'B', 'C')]
        def compare(a, b):
            return {'relation': 'different_event' if {a.title, b.title} == {'A', 'C'} else 'same_event'}
        with patch('notice_intelligence.events.compare_notices', side_effect=compare):
            groups = group_candidates(notices)
        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_rich_representative_keeps_all_departments(self):
        first = self.notice('2026 해양 개발 캠프', source_url='https://example.invalid/a', department='기계', body_text='모집 안내')
        second = self.notice('2026 해양 개발 캠프', source_url='https://example.invalid/b', department='조선해양',
                             body_text='모집 기간: 11월. 신청 방법: 온라인. 참가 대상: 대학생. 장소: 부산.',
                             attachment_urls=['https://example.invalid/form.pdf'], metadata_complete=True)
        result = select_representative([first, second])
        self.assertIs(result['winner'], second)
        self.assertEqual(result['departments'], ['기계', '조선해양'])
        self.assertEqual(len(result['source_urls']), 2)
        self.assertEqual(first.body_text, '모집 안내')


class MetadataTests(unittest.TestCase):
    def test_relative_attachment_and_explicit_publication_date(self):
        soup = BeautifulSoup('''<dl><dt>작성일</dt><dd>2026.10.26</dd></dl>
            <dd class="artclInsert"><a href="../download.do?id=1">신청서.pdf</a></dd>''', 'html.parser')
        data = extract_metadata(soup, 'https://example.invalid/board/view/1')
        self.assertEqual(data['publication_time'], '2026-10-26')
        self.assertEqual(data['attachment_links'][0]['url'], 'https://example.invalid/board/download.do?id=1')

    def test_body_deadline_is_not_invented_as_publication_time(self):
        data = extract_metadata(BeautifulSoup('<p>신청마감 2026.11.10</p>', 'html.parser'), 'https://example.invalid/1')
        self.assertIsNone(data['publication_time'])
        self.assertFalse(data['attachment_scan_complete'])


class ModelAndRoutingTests(unittest.TestCase):
    def test_model_version_and_raw_prediction_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'model.gz'
            data = {'schema_version': 1, 'normalization': NORMALIZATION_VERSION, 'classes': ['교육/특강/프로그램', '해당없음'],
                    'terms': {}, 'intercepts': [5.0, 0.0], 'weights': [[], []], 'thresholds': {'교육/특강/프로그램': .9}}
            with gzip.open(path, 'wt', encoding='utf-8') as handle: json.dump(data, handle)
            result = TitleClassifier(path).predict('새로운 특강 프로그램 모집 안내')
            self.assertTrue(result['accepted'])
            self.assertTrue(result['requires_review'])
            data['normalization'] = 'old_version'
            with gzip.open(path, 'wt', encoding='utf-8') as handle: json.dump(data, handle)
            with self.assertRaises(ValueError): TitleClassifier(path)

    def test_topic_does_not_override_the_activity(self):
        classifier = types.SimpleNamespace(data={'routing_approved': True}, predict=lambda _: {
            'category': '교육/특강/프로그램', 'score': .98, 'accepted': True})
        hint = category_hint('학술적 글쓰기 특강 참가자 모집', {'글쓰기': '[공모전] 문학/수기/에세이'}, classifier)
        self.assertEqual(hint['category'], '교육/특강/프로그램')

    def test_uncalibrated_class_does_not_bypass_gpt(self):
        classifier = types.SimpleNamespace(data={'routing_approved': True}, predict=lambda _: {
            'category': '[공모전] 기타', 'score': .99, 'accepted': False})
        hint = category_hint('2026 우주 해커톤 대회 개최', {'대회 개최': '[공모전] 기타'}, classifier)
        self.assertIsNone(hint['category'])

    def test_scholarship_payment_is_not_recruitment(self):
        hint = category_hint('11월 BK연구장학금 지급일 안내', {'장학금': '장학금'})
        self.assertEqual(hint['category'], '해당없음')

    def test_internship_recruitment_is_not_hidden_by_registration_word(self):
        hint = category_hint('현장실습학기제 실습기관, 학생 모집 및 수강신청 안내', {'현장실습': '취업 정보'})
        self.assertNotEqual(hint['category'], '해당없음')


class ShadowTests(unittest.TestCase):
    def test_observer_failure_cannot_interrupt_posting(self):
        with patch.dict(os.environ, {'NOTICE_INTELLIGENCE_MODE': 'shadow'}), \
             patch('notice_intelligence.shadow._recorder', side_effect=OSError('private-url')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            observe_notice(types.SimpleNamespace(title='test'), '기계')
        self.assertNotIn('private-url', output.getvalue())

    def test_private_observations_and_proposal_have_no_post_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('notice_intelligence.shadow.configured_classifier', return_value=None):
                recorder = ShadowRecorder(temp)
            ann = types.SimpleNamespace(title='2026년 제3기 해양 개발 프로그램 모집', original_title='⭐2026년 제3기 해양 개발 프로그램 모집',
                                        content_text='신청 방법: 온라인. 모집 기간: 11월.', url='https://example.invalid/1',
                                        metadata={'attachment_scan_complete': True, 'attachment_links': []})
            recorder.notice(ann, '기계'); recorder.category(ann.title, '교육/특강/프로그램', 'test'); recorder.finish()
            proposal = json.loads((Path(temp) / '.runtime/intelligence/last-cycle-proposal.json').read_text())
            self.assertTrue(proposal['proposal_only']); self.assertFalse(proposal['posts_changed'])
            self.assertEqual(recorder.connection.execute('SELECT COUNT(*) FROM notices').fetchone()[0], 1)
            self.assertEqual(recorder.connection.execute('SELECT COUNT(*) FROM categories').fetchone()[0], 1)
            recorder.connection.close()


class DuplicateContractTests(unittest.TestCase):
    def run_duplicate(self, query, existing):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'titles.txt'
            timestamp = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
            path.write_text(f'{timestamp}${existing}\n', encoding='utf-8')
            with patch.dict(os.environ, {'NOTICE_DUPLICATE_MODE': 'hybrid'}), \
                 patch.object(duplicate_checker, 'check_title_similarity', return_value='중복 아님') as gpt:
                result = duplicate_checker.is_recent_title_duplicate(query, str(path))
            return result, gpt.call_count

    def test_high_similarity_different_round_is_new(self):
        result, calls = self.run_duplicate('2026학년도 제4회 특별초청강연', '2026학년도 제2회 특별초청강연')
        self.assertEqual(result, '중복 아님'); self.assertEqual(calls, 0)

    def test_same_specific_event_can_be_local(self):
        result, calls = self.run_duplicate('[학부] 2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집',
                                           '2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집')
        self.assertEqual(result, '중복'); self.assertEqual(calls, 0)

    def test_a_high_ratio_alone_does_not_discard_generic_notice(self):
        result, calls = self.run_duplicate('대학원 장학금 신청 안내', '대학원 장학금 신청 안내')
        self.assertEqual(result, '중복 아님'); self.assertEqual(calls, 1)


class GPTContractTests(unittest.TestCase):
    def load_adapter(self):
        path = Path(__file__).resolve().parents[1] / 'gpt_client.py'
        spec = importlib.util.spec_from_file_location('tested_gpt_adapter', path)
        module = importlib.util.module_from_spec(spec)
        with patch('openai.OpenAI') as factory, patch('api_accounting.ApiAccounting') as accounting:
            accounting.return_value.cached.return_value = None
            factory.return_value = MagicMock()
            spec.loader.exec_module(module)
        return module

    def response(self, adapter, value):
        adapter.client.chat.completions.create.return_value = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(value)))])

    def test_category_string_and_body_are_preserved(self):
        adapter = self.load_adapter(); self.response(adapter, {'category': '교육/특강/프로그램', 'needs_body': False, 'reason': '특강'})
        self.assertEqual(adapter.answer_gpt('제목\n본문'), '교육/특강/프로그램')
        args = adapter.client.chat.completions.create.call_args.kwargs
        self.assertIn('본문', args['messages'][1]['content'])
        self.assertEqual(args['response_format']['type'], 'json_schema')

    def test_unknown_category_is_rejected(self):
        adapter = self.load_adapter(); self.response(adapter, {'category': '임의 카테고리'})
        with self.assertRaises(ValueError): adapter.answer_gpt('제목')

    def test_context_abstention_keeps_legacy_other_board_contract(self):
        adapter = self.load_adapter(); self.response(adapter, {'category': '판단보류', 'needs_body': True, 'reason': '자료 부족'})
        self.assertEqual(adapter.answer_gpt('제목'), '해당없음')

    def test_duplicate_requires_a_valid_match_index(self):
        adapter = self.load_adapter(); self.response(adapter, {'relation': 'same_event', 'matched_index': -1})
        with self.assertRaises(ValueError): adapter.check_title_similarity('2026 해양 캠프', ['2026 해양 캠프 모집'])

    def test_new_year_never_calls_api_for_old_occurrence(self):
        adapter = self.load_adapter()
        self.assertEqual(adapter.check_title_similarity('2026년 제3기 캠프', ['2025년 제3기 캠프']), '중복 아님')
        adapter.client.chat.completions.create.assert_not_called()

    def test_department_sensitive_recruitment_never_relies_on_llm_guess(self):
        adapter = self.load_adapter()
        self.assertEqual(adapter.check_title_similarity('2026학년도 2학기 근로장학생 선발 안내',
                                                       ['2026학년도 2학기 근로장학생 선발 안내']), '중복 아님')
        adapter.client.chat.completions.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
