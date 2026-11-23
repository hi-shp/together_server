"""Protect actual department exams and electives from a common-title match."""
import ast,re,unittest
from pathlib import Path
from unittest.mock import MagicMock
from together.notices.intelligence.text import requires_department_evidence,normalize_title
from together.operations.events import duplicate_review

class DepartmentFirstNoticeTests(unittest.TestCase):
    def decide(self,title,context=None):
        path=Path(__file__).resolve().parents[1]/'together/integrations/openai_client.py'
        fn=next(n for n in ast.parse(path.read_text(encoding='utf8')).body if isinstance(n,ast.FunctionDef) and n.name=='check_title_similarity')
        paid=MagicMock(side_effect=AssertionError('No model may suppress an unproved department notice'))
        scope={'requires_department_evidence':requires_department_evidence,'normalize_title':normalize_title,'duplicate_review':duplicate_review,'_decision':paid,'re':re}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),scope)
        return scope['check_title_similarity'](title,['2026학년도 2학기 학부 졸업시험 안내'],context_provider=(lambda titles:context) if context else None)

    def test_department_graduation_exam_is_not_replaced_by_another_department(self):
        self.assertEqual(self.decide('2026-2학기 학부 졸업시험 안내'),'중복 아님')

    def test_department_elective_is_not_replaced_by_a_common_winter_timetable(self):
        self.assertEqual(self.decide('2026 겨울도약수업 전공선택 교과목 안내'),'중복 아님')

    def test_the_same_source_and_title_can_still_be_reconciled_without_reposting(self):
        title='2026-2학기 학부 졸업시험 안내';url='https://example.invalid/exam'
        context={'new_notice':{'source_url':url},'recent_notices':[{'source_url':url,'title':title}]}
        self.assertEqual(self.decide(title,context),'중복')

    def test_readmission_cutoff_is_not_merged_without_department_evidence(self):
        self.assertEqual(self.decide('2027-1학기 재입학 안내- 12.4(금) 까지'),'중복 아님')
