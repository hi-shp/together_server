import gzip,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from together.storage.decision_samples import record_decision,observe_decision

class DecisionSampleTests(unittest.TestCase):
    def test_same_input_replay_preserves_result_origin_without_sdk_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            request={'messages':[{'role':'user','content':'공지 본문'}],'response_format':{},'api_key':'not_saved'}
            context={'source_url':'https://example.invalid/1','department':'학부','credential':'not_saved'}
            one=record_decision(temp,'model',request,{'category':'장학금'},'api',context,{'prompt_tokens':100})
            two=record_decision(temp,'model',request,{'category':'장학금'},'cache',context)
            self.assertNotEqual(one,two)
            root=Path(temp)/'.runtime/intelligence/decision-samples'
            rows=[json.loads(gzip.decompress(p.read_bytes())) for p in (root/'bodies').rglob('*.gz')]
            self.assertEqual({v['origin'] for v in rows},{'api','cache'})
            self.assertNotIn('not_saved',json.dumps(rows))
            self.assertTrue(all(not v['is_human_label'] for v in rows))

    def test_storage_failure_never_raises_into_api_retry(self):
        import together.operations.events
        with patch('together.storage.decision_samples.record_decision',side_effect=OSError()),patch('together.operations.events.event') as event:
            observe_decision('unused','model',{}, {}, 'api')
            event.assert_called_once()

if __name__=='__main__':unittest.main()
