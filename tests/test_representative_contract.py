from datetime import datetime,timedelta
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from together.notices import history
from together.notices.intelligence.representative import RepresentativeUnconfirmed


class RepresentativeContractTests(unittest.TestCase):
    TITLE='2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집'
    def check(self,proof,days=1,mode='hybrid'):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'titles.txt'
            original=f'{datetime.now()-timedelta(days=days):%Y-%m-%d %H:%M:%S}${self.TITLE}\n'
            path.write_text(original,encoding='utf-8')
            with patch.dict(os.environ,{'NOTICE_DUPLICATE_MODE':mode}),patch.object(history,'event'),patch.object(history,'check_title_similarity',return_value='중복 아님'),patch.object(history,'duplicate_match') as matched:
                matched.get.return_value={'title':self.TITLE}
                try:
                    return history.is_recent_title_duplicate(self.TITLE,str(path),representative_check=lambda _:proof)
                finally:
                    self.assertEqual(path.read_text(encoding='utf-8'),original)

    def test_string_history_does_not_allow_zero_representatives(self):
        self.assertEqual(self.check({'status':'missing'}),'중복 아님')
    def test_unposted_first_notice_goes_to_normal_publish_and_registration(self):
        self.assertEqual(self.check({'status':'first_publication'}),'중복 아님')
    def test_a_verified_actual_representative_allows_duplicate_suppression(self):
        self.assertEqual(self.check({'status':'verified'}),'중복')
    def test_unknown_school_read_never_becomes_repost_permission(self):
        with self.assertRaises(RepresentativeUnconfirmed):self.check({'status':'pending'})
    def test_hybrid_uses_the_two_week_dispatch_window(self):
        self.assertEqual(self.check({'status':'verified'},days=12),'중복')
    def test_legacy_window_is_not_changed(self):
        self.assertEqual(self.check({'status':'verified'},days=12,mode='legacy'),'중복 아님')

    def test_named_exact_title_within_window_survives_a_thousand_dispatch_rows(self):
        self.assertEqual(self.crowded_history(13,'hybrid'),'중복')

    def test_exact_title_outside_dispatch_window_does_not_suppress_a_first_notice(self):
        self.assertEqual(self.crowded_history(15,'hybrid'),'중복 아님')

    def test_extended_exact_title_requires_context_decision_before_suppression(self):
        self.assertEqual(self.crowded_history(13,'hybrid',api_same=False),'중복 아님')

    def test_legacy_thousand_row_candidate_boundary_is_unchanged(self):
        self.assertEqual(self.crowded_history(9,'legacy'),'중복 아님')

    def crowded_history(self,days,mode,api_same=True):
        title='[위스콘신대학교] University of Wisconsin-Stout 2027 Winter 글로벌 리더십 프로그램 안내'
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'titles.txt';now=datetime.now()
            rows=[f'{now-timedelta(days=days):%Y-%m-%d %H:%M:%S}${title}\n']
            rows += [f'{now:%Y-%m-%d %H:%M:%S}$2026년 일반 학사 안내 {i}\n' for i in range(1001)]
            p.write_text(''.join(rows),encoding='utf8')
            decide=lambda _,titles,**kw: '중복' if api_same and title in titles else '중복 아님'
            with patch.dict(os.environ,{'NOTICE_DUPLICATE_MODE':mode}),patch.object(history,'event'),patch.object(history,'check_title_similarity',side_effect=decide):
                return history.is_recent_title_duplicate(title,str(p),representative_check=lambda _: {'status':'verified'})

    def test_a_history_alias_does_not_become_a_duplicate_new_post(self):
        actual_title='2026년 제3기 해양정보 로봇 개발 캠프 모집'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'titles.txt';path.write_text(f'{datetime.now():%Y-%m-%d %H:%M:%S}${self.TITLE}\n',encoding='utf-8')
            proofs=iter([{'status':'recheck','_actual_candidates':[{'title':actual_title,'source_url':'https://ee.pusan.ac.kr/posted','body_excerpt':'기존 실제 원문'}]},{'status':'verified'}])
            with patch.dict(os.environ,{'NOTICE_DUPLICATE_MODE':'hybrid'}),patch.object(history,'event'),patch.object(history,'check_title_similarity',return_value='중복') as model:
                result=history.is_recent_title_duplicate(self.TITLE,str(path),representative_check=lambda _:next(proofs))
            self.assertEqual(result,'중복')
            self.assertEqual(model.call_args.args[1],[actual_title])

if __name__=='__main__':unittest.main()
