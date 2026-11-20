import copy
import unittest
from together.notices.intelligence.recovery import _same_material


class RecoveryPinTests(unittest.TestCase):
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
