import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from together.integrations.work_client import WorkClient, WorkUnavailable
from together.notices.intelligence.prompts import category_request, duplicate_request
from together.notices.intelligence.policy import CATEGORIES
from together.storage.api_usage import ApiAccounting
from together.storage.decision_provider import DecisionProvider, CreditUnavailable, credit_exhausted


class QuotaError(Exception):
    body = {'error': {'code': 'insufficient_quota'}}
    status_code = 429


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / '.runtime').mkdir()
        self.config = {'policy': 'api_credit_then_work', 'balance_observed_at': 0,
                       'credit_grants': [{'balance_usd': 10, 'expires_at': '2099-01-01T00:00:00+00:00'}],
                       'work_model': 'subscription-model', 'work_binary': __file__,
                       'work_review_below': {'notice_category': .95, 'duplicate_decision': .95}}
        self.save()
        self.request = category_request('학생 참여 특강 신청', CATEGORIES, '학생을 대상으로 특강 참여자를 모집합니다.')
        self.answer = {'category': '교육/특강/프로그램', 'notice_type': 'notice', 'needs_body': False, 'reason': '학생 특강 참여 신청'}

    def save(self):
        (self.base / '.runtime/decision-provider.json').write_text(json.dumps(self.config), encoding='utf-8')

    def adapter(self):
        spec = importlib.util.spec_from_file_location('work_transition_adapter', Path(__file__).parents[1] / 'together/integrations/openai_client.py')
        module = importlib.util.module_from_spec(spec)
        with patch('openai.OpenAI'):
            spec.loader.exec_module(module)
        module.BASE_DIR = self.base
        module.client.with_options.return_value = module.client
        return module

    def fake_cli(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
        output = {name: self.answer for name in schema['required']}
        Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(output), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 900, 'output_tokens': 100, 'cached_input_tokens': 200}}))

    def test_quota_is_distinct_from_rate_limit_and_network_failure(self):
        self.assertTrue(credit_exhausted(QuotaError()))
        rate = SimpleNamespace(body={'error': {'code': 'rate_limit_exceeded'}})
        self.assertFalse(credit_exhausted(rate))
        self.assertFalse(credit_exhausted(TimeoutError()))

    def test_credit_state_survives_restart(self):
        provider = DecisionProvider(self.base)
        provider.mark_exhausted('provider_confirmed_credit_exhausted')
        self.assertTrue(DecisionProvider(self.base).exhausted)
        with self.assertRaises(CreditUnavailable):
            DecisionProvider(self.base).reserve('gpt-5.4-mini', self.request)

    def test_uses_old_grant_after_expiring_grant_without_a_daily_cap(self):
        self.config['credit_grants'] = [{'balance_usd': 100, 'expires_at': '2020-01-01T00:00:00+00:00'},
                                        {'balance_usd': 2.3, 'expires_at': '2099-01-01T00:00:00+00:00'}]
        self.save()
        p = DecisionProvider(self.base)
        key = p.reserve('gpt-5.4-mini', self.request)
        self.assertTrue(key)
        self.assertFalse(p.exhausted)

    def test_no_authorized_funds_switches_to_work(self):
        self.config['credit_grants'][0]['balance_usd'] = 0
        self.save()
        with self.assertRaises(CreditUnavailable):
            DecisionProvider(self.base).reserve('gpt-5.4-mini', self.request)
        self.assertTrue(DecisionProvider(self.base).exhausted)

    def test_uncertain_paid_outcome_remains_reserved(self):
        p = DecisionProvider(self.base)
        key = p.reserve('gpt-5.4-mini', self.request)
        p.settle(key, error=TimeoutError())
        with p.db() as db:
            self.assertEqual(db.execute('SELECT state FROM reservations').fetchone()[0], 'pending')
        p.settle(key, error=QuotaError())
        with p.db() as db:
            self.assertEqual(db.execute('SELECT state FROM reservations').fetchone()[0], 'settled')

    def test_subscription_results_never_pose_as_api_cache(self):
        accounting = ApiAccounting(self.base)
        accounting.store('model', self.request, dict(self.answer, _work_remote=True))
        self.assertIsNone(accounting.cached('model', self.request))

    def test_work_cache_and_batched_same_request_do_not_repeat_inference(self):
        work = WorkClient(self.base, self.config)
        with patch('together.integrations.work_client.subprocess.run', side_effect=self.fake_cli) as run:
            results = work.decide_many([self.request, self.request])
            self.assertEqual(len(results), 2)
            self.assertFalse(results[0]['_work_cached'])
            self.assertTrue(work.decide(self.request)['_work_cached'])
            self.assertEqual(run.call_count, 1)
        with work.db() as db:
            self.assertEqual(db.execute('SELECT items,input,output,cached FROM attempts').fetchone(), (1, 900, 100, 200))

    def test_no_api_keys_tools_or_chat_history_in_subscription_worker(self):
        with patch.dict(os.environ, {'GPT_API_KEY': 'private-test', 'OPENAI_API_KEY': 'private-test', 'API_USAGE_CONTEXT': 'crawler'}), patch('together.integrations.work_client.subprocess.run', side_effect=self.fake_cli):
            WorkClient(self.base, self.config).decide(self.request)
        self.assertNotIn('GPT_API_KEY', self.kwargs['env'])
        self.assertNotIn('OPENAI_API_KEY', self.kwargs['env'])
        self.assertIn('forced_login_method="chatgpt"', self.command)
        self.assertIn('shell_tool', self.command)
        self.assertIn('--ignore-user-config', self.command)
        self.assertEqual(self.kwargs['timeout'], 55)
        self.assertNotIn('private-test', self.kwargs['input'])

    def test_bad_json_keeps_unknown_attempt_and_backoff_without_cache(self):
        work = WorkClient(self.base, self.config)
        with patch('together.integrations.work_client.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='')) as run:
            with self.assertRaises(WorkUnavailable): work.decide(self.request)
            with self.assertRaises(WorkUnavailable): work.decide(self.request)
            self.assertEqual(run.call_count, 1)
        self.assertIsNone(work.cached(self.request))
        with work.db() as db:
            self.assertEqual(db.execute('SELECT state FROM attempts').fetchone()[0], 'unconfirmed')

    def test_empty_batch_never_calls_work(self):
        with patch('together.integrations.work_client.subprocess.run') as run:
            with self.assertRaises(ValueError): WorkClient(self.base, self.config).decide_many([])
            run.assert_not_called()

    def test_same_event_without_a_matched_candidate_is_not_cached(self):
        request = duplicate_request('새 프로그램 안내', ['기존 프로그램 안내'])
        work = WorkClient(self.base, self.config)
        self.answer = {'relation': 'same_event', 'matched_index': -1, 'reason': '일치 후보를 지정하지 못한 잘못된 응답'}
        with patch('together.integrations.work_client.subprocess.run', side_effect=self.fake_cli):
            with self.assertRaises(ValueError): work.decide(request)
        self.assertIsNone(work.cached(request))

    def test_new_work_items_keep_the_same_minimal_prefix_context(self):
        work = WorkClient(self.base, self.config)
        another = category_request('학생 특강 두 번째 신청', CATEGORIES, '다른 특강 참가자를 모집합니다.')
        with patch('together.integrations.work_client.subprocess.run', side_effect=self.fake_cli) as run:
            work.decide(self.request)
            work.decide(another)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs['cwd'], run.call_args_list[1].kwargs['cwd'])
        self.assertEqual(run.call_args_list[0].kwargs['env']['CODEX_HOME'], run.call_args_list[1].kwargs['env']['CODEX_HOME'])

    def test_api_is_used_first_and_credit_exhaustion_switches_once(self):
        api = self.adapter()
        api.client.chat.completions.create.side_effect = QuotaError()
        remote = dict(self.answer, _work_remote=True, _work_model='subscription-model')
        with patch('together.integrations.work_client.WorkClient.decide', return_value=remote), patch.object(api, 'event'):
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_work_remote'])
            self.assertTrue(DecisionProvider(self.base).exhausted)
            restarted = self.adapter()
            self.assertTrue(restarted._decision(restarted.CATEGORY_MODEL, self.request)['_work_remote'])
        api.client.chat.completions.create.assert_called_once()
        restarted.client.chat.completions.create.assert_not_called()

    def test_api_response_uses_existing_request_and_completion_limit(self):
        api = self.adapter()
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.answer)), finish_reason='stop')],
                                   usage=SimpleNamespace(prompt_tokens=40, completion_tokens=20, prompt_tokens_details=None))
        api.client.chat.completions.create.return_value = response
        with patch('together.integrations.work_client.WorkClient.decide') as work:
            self.assertEqual(api._decision(api.CATEGORY_MODEL, self.request), self.answer)
            work.assert_not_called()
        call = api.client.chat.completions.create.call_args.kwargs
        self.assertEqual(call['messages'], self.request['messages'])
        self.assertEqual(call['max_completion_tokens'], 500)
        api.client.with_options.assert_called_once_with(max_retries=0)

    def test_confident_local_after_exhaustion_does_not_call_work(self):
        DecisionProvider(self.base).mark_exhausted('test')
        api = self.adapter()
        c = MagicMock(active=True)
        c.begin.return_value = {'prediction': {'confidence': .99}}
        c.needs_review.return_value = False
        c.local_result.return_value = dict(self.answer, _local_primary=True)
        with patch('together.learning.campaign.Campaign', return_value=c), patch('together.integrations.work_client.WorkClient.decide') as work:
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_local_primary'])
            work.assert_not_called()
        api.client.chat.completions.create.assert_not_called()

    def test_uncertain_local_after_exhaustion_uses_work(self):
        DecisionProvider(self.base).mark_exhausted('test')
        api = self.adapter()
        c = MagicMock(active=True)
        c.begin.return_value = {'prediction': {'confidence': .7}}
        c.needs_review.return_value = True
        remote = dict(self.answer, _work_remote=True, _work_model='subscription-model')
        with patch('together.learning.campaign.Campaign', return_value=c), patch('together.integrations.work_client.WorkClient.decide', return_value=remote) as work:
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_work_remote'])
            work.assert_called_once()
        api.client.chat.completions.create.assert_not_called()

    def test_work_outage_retains_fallback_review_and_does_not_retry_api(self):
        DecisionProvider(self.base).mark_exhausted('test')
        api = self.adapter()
        with patch('together.integrations.work_client.WorkClient.decide', side_effect=WorkUnavailable('offline')) as work, patch.object(api, 'event'):
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_local_fallback'])
            self.assertEqual(len(__import__('together.storage.fallback_reviews', fromlist=['FallbackReviews']).FallbackReviews(self.base).pending()), 1)
            work.assert_called_once()
        api.client.chat.completions.create.assert_not_called()

    def test_confident_notice_at_the_exact_exhaustion_boundary_stays_local(self):
        api = self.adapter()
        api.client.chat.completions.create.side_effect = QuotaError()
        c = MagicMock(active=True)
        c.begin.return_value = {'prediction': {'confidence': .99}}
        c.needs_review.return_value = False
        c.local_result.return_value = dict(self.answer, _local_primary=True)
        with patch('together.learning.campaign.Campaign', return_value=c), patch('together.integrations.work_client.WorkClient.decide') as work, patch.object(api, 'event'):
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_local_primary'])
            work.assert_not_called()
        self.assertTrue(DecisionProvider(self.base).exhausted)

    def test_transient_api_failure_keeps_probe_backoff_after_work_success(self):
        api = self.adapter()
        api.client.chat.completions.create.side_effect = ConnectionError('temporary network failure')
        remote = dict(self.answer, _work_remote=True, _work_model='subscription-model')
        another = category_request('다른 학생 프로그램 모집', CATEGORIES, '학생 참여자를 모집합니다.')
        with patch('together.integrations.work_client.WorkClient.decide', return_value=remote), patch.object(api, 'event'):
            self.assertTrue(api._decision(api.CATEGORY_MODEL, self.request)['_work_remote'])
            self.assertTrue(api._decision(api.CATEGORY_MODEL, another)['_work_remote'])
            self.assertTrue(api._decision(api.CATEGORY_MODEL, another, require_remote=True)['_work_remote'])
        self.assertFalse(DecisionProvider(self.base).exhausted)
        self.assertEqual(api.client.chat.completions.create.call_count, 1)

