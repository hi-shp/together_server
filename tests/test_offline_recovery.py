import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from together.storage.fallback_reviews import FallbackReviews
from together.storage.api_usage import ApiAccounting
from together.notices.intelligence.offline import local_decision
from together.notices.intelligence.prompts import category_request, duplicate_request
from together.notices.intelligence.policy import CATEGORIES
from together.operations.events import set_notice, clear_notice


class OfflineRecoveryTests(unittest.TestCase):
    def adapter(self, root):
        spec = importlib.util.spec_from_file_location('offline_adapter', Path(__file__).resolve().parents[1] / 'together/integrations/openai_client.py')
        module = importlib.util.module_from_spec(spec)
        with patch('openai.OpenAI'):
            spec.loader.exec_module(module)
        module.BASE_DIR = Path(root)
        return module

    def test_network_outage_continues_without_caching_teacher_result(self):
        with tempfile.TemporaryDirectory() as root:
            module = self.adapter(root)
            module.client.chat.completions.create.side_effect = TimeoutError()
            request = category_request('2026 저작권 교육 운영 및 신청 안내', CATEGORIES, '교육에 참가할 학생을 모집합니다.')
            set_notice('https://semi.pusan.ac.kr/bbs/semi/1/99/artclView.do', '반도체공학과', '2026 저작권 교육')
            with patch.object(module, 'event'):
                first = module._decision('test', request)
                second = module._decision('test', request)
            clear_notice()
            self.assertEqual(first['category'], '교육/특강/프로그램')
            self.assertTrue(first['_local_fallback'])
            self.assertEqual(first, second)
            self.assertEqual(module.client.chat.completions.create.call_count, 1)
            accounting = ApiAccounting(root)
            accounting.store('test', request, first)
            self.assertIsNone(accounting.cached('test', request))
            queue = FallbackReviews(root).pending()
            self.assertEqual(len(queue), 1)
            self.assertEqual(json.loads(queue[0]['context'])['department'], '반도체공학과')

    def test_recovered_api_disagrees_with_fallback_and_is_retained_for_correction(self):
        from together.notices.intelligence import recovery
        with tempfile.TemporaryDirectory() as root:
            request = category_request('장학생 선발 결과', CATEGORIES, '')
            queue = FallbackReviews(root)
            queue.record('test', request, {'category': '장학금'}, {'source_url': 'https://example.org/post'}, TimeoutError())
            remote = {'category': '해당없음', 'notice_type': 'notice', 'needs_body': False, 'reason': '선발 결과'}
            adapter = types.SimpleNamespace(_decision=MagicMock(return_value=remote))
            with patch.dict('sys.modules', {'together.integrations.openai_client': adapter}), patch.object(recovery, 'move_category', return_value={'done': False, 'reason': 'manual_edit_protected'}) as correction:
                recovery.review_pending(MagicMock(), root)
                recovery.review_pending(MagicMock(), root)
            self.assertEqual(adapter._decision.call_count, 1)
            self.assertEqual(correction.call_count, 2)
            self.assertEqual(queue.summary(), {'correction_pending': 1})
            self.assertEqual(json.loads(queue.pending()[0]['reviewed_result']), remote)

    def test_title_similarity_alone_does_not_drop_different_occurrences(self):
        with tempfile.TemporaryDirectory() as root:
            request = duplicate_request('2026년 제3기 창업 캠프 모집', ['2026년 제4기 창업 캠프 모집'])
            result = local_decision(request, root)
            self.assertNotEqual(result['relation'], 'same_event')
            request = duplicate_request('2026 근로장학생 모집', ['2026 근로장학생 모집'], {'new_notice': {'department': '화학과', 'body_excerpt': '같은 내용 ' * 40}, 'recent_notices': [{'department': '물리학과', 'body_excerpt': '같은 내용 ' * 40}]})
            self.assertNotEqual(local_decision(request, root)['relation'], 'same_event')

    def test_identical_body_and_occurrence_can_be_locally_merged(self):
        with tempfile.TemporaryDirectory() as root:
            request = duplicate_request('2026 제3기 창업 캠프 모집', ['2026 제3기 창업 캠프 안내'], {'new_notice': {'body_excerpt': '11월 3일 학생 모집 같은 프로그램 내용 ' * 20}, 'recent_notices': [{'body_excerpt': '11월 3일 학생 모집 같은 프로그램 내용 ' * 20}]})
            result = local_decision(request, root)
            self.assertEqual(result['relation'], 'same_event')
            self.assertEqual(result['matched_index'], 0)

    def test_material_comparison_requires_attachments_and_pinned_state_preservation(self):
        from together.notices.intelligence.recovery import _same_material
        a = {'subject': '행사', 'body_html': '<p>내용</p>', 'notice': False, 'files': [{'name': '안내.pdf', 'sha256': 'abc'}]}
        self.assertTrue(_same_material(a, dict(a)))
        self.assertFalse(_same_material(a, dict(a, files=[])))
        self.assertFalse(_same_material(a, dict(a, notice=True)))

    def test_changed_deadline_is_not_hidden_by_same_body_during_outage(self):
        with tempfile.TemporaryDirectory() as root:
            request=duplicate_request('2026 창업 캠프 모집(~11/12)', ['2026 창업 캠프 모집(~11/05)'], {'new_notice':{'body_excerpt':'동일한 모집 안내 본문 '*30},'recent_notices':[{'body_excerpt':'동일한 모집 안내 본문 '*30}]})
            self.assertNotEqual(local_decision(request,root)['relation'],'same_event')

    def test_uncertain_copy_is_not_posted_twice(self):
        from together.notices.intelligence import recovery
        with tempfile.TemporaryDirectory() as root:
            source='https://example.org/notice'
            receipt={'source_url': source, 'state':'verified', 'board':'장학금', 'article_url':'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2'}
            snapshot={'subject':'행사', 'body_html':'<p>https://example.org/notice</p>', 'notice':False, 'files':[], 'signature':'original'}
            path=Path(root)/'.runtime/intelligence/recovery/abc.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'phase':'copy_submitting','original':snapshot,'original_receipt':receipt,'original_record':{'signature':'original'}},ensure_ascii=False))
            service=MagicMock()
            with patch.object(recovery,'known_receipt',return_value=receipt),patch.object(recovery,'_links',return_value={}),patch.object(recovery,'PublishedNoticeStore'),patch.object(recovery,'PlatoPostEditor'):
                result=recovery.move_category(service,root,source,'교육/특강/프로그램','abc')
            self.assertFalse(result['done'])
            self.assertEqual(result['reason'],'copy_acceptance_unknown_original_preserved')
            service._http.post_once.assert_not_called()

    def test_manual_change_is_not_overwritten_or_deleted(self):
        from together.notices.intelligence import recovery
        with tempfile.TemporaryDirectory() as root:
            source='https://example.org/notice'
            receipt={'source_url':source,'state':'verified','board':'장학금','article_url':'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2'}
            service=MagicMock()
            with patch.object(recovery,'known_receipt',return_value=receipt),patch.object(recovery,'PublishedNoticeStore') as index,patch.object(recovery,'PlatoPostEditor') as editor:
                index.return_value.load.return_value=[{'article_url':receipt['article_url'],'signature':'original'}]
                editor.return_value.read.return_value={'signature':'edited_by_user','body_html':source}
                result=recovery.move_category(service,root,source,'교육/특강/프로그램','abc')
            self.assertFalse(result['done'])
            self.assertEqual(result['reason'],'manual_edit_or_missing_original_proof')
            service._http.post_once.assert_not_called()


if __name__ == '__main__':
    unittest.main()
