import copy
import unittest
from together.notices.intelligence.recovery import _same_material, _has_source


class RecoveryPinTests(unittest.TestCase):
    def test_source_query_is_decoded_and_different_article_is_rejected(self):
        source='https://school.invalid/view?mgr_seq=10&board_seq=1234'
        self.assertTrue(_has_source('<p>본문 링크 : <a href="https://school.invalid/view?mgr_seq=10&amp;board_seq=1234">원문</a></p>',source))
        self.assertTrue(_has_source('<p>'+source+'</p>',source))
        self.assertFalse(_has_source('<a href="'+source+'5">다른 글</a>',source))
        self.assertFalse(_has_source('<p>https://school.invalid/other</p>',source))
    def snapshot(self, notice):
        return {'subject':'학술지 논문 모집','body_html':'<p>같은 원문</p>','notice':notice,'files':[{'name':'안내.hwp','sha256':'verified-bytes'}]}

    def test_unavailable_and_unchecked_pin_are_equivalent_but_pinned_is_protected(self):
        for a,b in ((None,False),(False,None),(False,False),(None,None),(True,True)):
            self.assertTrue(_same_material(self.snapshot(a),self.snapshot(b)))
        for a,b in ((True,None),(None,True),(True,False),(False,True)):
            self.assertFalse(_same_material(self.snapshot(a),self.snapshot(b)))

    def test_subject_body_and_all_attachment_hashes_must_still_match(self):
        original=self.snapshot(False)
        for field,value in [('subject','다른 제목'),('body_html','<p>빠진 내용</p>'),('files',[]),('files',[{'name':'안내.hwp','sha256':'changed-bytes'}])]:
            other=self.snapshot(None);other[field]=value
            self.assertFalse(_same_material(original,other))
