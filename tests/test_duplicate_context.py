"""제목 전체의 대괄호와 판단 당시 본문 연결을 검사한다."""

import ast
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from together.notices.intelligence.text import requires_department_evidence, normalize_title
from together.notices.history import comparison_title, calculate_similarity
from together.notices.intelligence.context import duplicate_context
from together.notices.intelligence.prompts import duplicate_request
from together.operations.events import duplicate_review, duplicate_review_reason


class ContextTests(unittest.TestCase):
    def test_brackets_around_the_whole_title_keep_the_event_name(self):
        first = "[기타] [2026 경기도 친환경차 부품개발 인력양성 교육생 모집]"
        second = "[공지] [2026 전혀 다른 창업 프로그램 모집]"
        self.assertIn("친환경차", comparison_title(first))
        self.assertLess(
            calculate_similarity(comparison_title(first), comparison_title(second)), 1
        )
        self.assertEqual(comparison_title(first, "legacy"), "")

    def test_current_later_observation_is_not_used_as_old_candidate_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / ".runtime/intelligence"
            folder.mkdir(parents=True)
            with sqlite3.connect(folder / "observations.sqlite3") as db:
                db.execute(
                    "CREATE TABLE notices (id INTEGER PRIMARY KEY, observed_at TEXT,title TEXT,source_url TEXT,department TEXT,body_excerpt TEXT)"
                )
                db.executemany(
                    "INSERT INTO notices VALUES (?,?,?,?,?,?)",
                    [
                        (
                            1,
                            "2026-10-27T10:00:00",
                            "영어 프로그램",
                            "https://example.invalid/old",
                            "기존 학과",
                            "기존 일정 " * 50,
                        ),
                        (
                            2,
                            "2026-10-27T11:00:00",
                            "영어 프로그램",
                            "https://example.invalid/new",
                            "다른 학과",
                            "나중 일정 " * 50,
                        ),
                    ],
                )
            ann = types.SimpleNamespace(
                title="영어 프로그램",
                url="https://example.invalid/current",
                content_text="현재 일정 " * 50,
            )
            data = duplicate_context(
                ann,
                "현재 학과",
                ["영어 프로그램"],
                {"영어 프로그램": datetime(2026, 10, 27, 10, 1)},
                root,
            )
            self.assertEqual(
                data["recent_notices"][0]["source_url"], "https://example.invalid/old"
            )
            self.assertIn("기존 일정", data["recent_notices"][0]["body_excerpt"])

    def test_uncertain_title_decision_uses_bodies_before_returning_a_final_result(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "together/integrations/openai_client.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "check_title_similarity"
        )
        decision = MagicMock(
            side_effect=[
                {
                    "relation": "same_event",
                    "matched_index": 0,
                    "reason": "프로그램 일정과 대상 일치",
                },
            ]
        )
        provider = MagicMock(
            return_value={
                "new_notice": {"body_excerpt": "이번 본문"},
                "recent_notices": [{"body_excerpt": "기존 본문"}],
            }
        )
        accounting = MagicMock()
        scope = {
            "requires_department_evidence": lambda _: False,
            "occurrence_conflict": lambda a, b: None,
            "duplicate_request": duplicate_request,
            "_decision": decision,
            "DUPLICATE_MODEL": "test",
            "BASE_DIR": "test",
            "ApiAccounting": MagicMock(return_value=accounting),
            "duplicate_review": duplicate_review,
            "duplicate_review_reason": duplicate_review_reason,
        }
        exec(
            compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), scope
        )
        self.assertEqual(
            scope["check_title_similarity"](
                "이번", ["기존"], context_provider=provider
            ),
            "중복",
        )
        self.assertEqual(decision.call_count, 1)
        payload = json.loads(
            decision.call_args_list[0].args[1]["messages"][1]["content"]
        )
        self.assertEqual(payload["new_notice"]["body_excerpt"], "이번 본문")
        self.assertIn("GPT 본문 검토", duplicate_review.get())
        duplicate_review.set("")
        duplicate_review_reason.set("")

    def protected_checker(self):
        source=Path(__file__).resolve().parents[1]/'together/integrations/openai_client.py'
        fn=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='check_title_similarity')
        decision=MagicMock()
        scope={'requires_department_evidence':requires_department_evidence,'normalize_title':normalize_title,'occurrence_conflict':lambda a,b:None,'duplicate_request':duplicate_request,'_decision':decision,'DUPLICATE_MODEL':'test','BASE_DIR':'test','ApiAccounting':MagicMock(),'duplicate_review':duplicate_review,'duplicate_review_reason':duplicate_review_reason}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),scope)
        return scope['check_title_similarity'],decision

    def test_each_candidate_is_checked_without_skipping_the_second(self):
        check, decision = self.protected_checker()
        decision.side_effect = [
            {"relation": "different_event", "matched_index": -1, "reason": "다른 행사"},
            {"relation": "same_event", "matched_index": 0, "reason": "본문 일정 일치"},
        ]
        provider = MagicMock(return_value=None)
        self.assertEqual(check("신규 행사", ["첫 후보", "둘째 후보", "셋째 후보"], context_provider=provider), "중복")
        self.assertEqual(decision.call_count, 2)
        self.assertEqual([call.args[0] for call in provider.call_args_list], [["첫 후보"], ["둘째 후보"]])
        payloads = [json.loads(call.args[1]["messages"][1]["content"]) for call in decision.call_args_list]
        self.assertEqual([p["candidate_titles"] for p in payloads], [["첫 후보"], ["둘째 후보"]])

    def test_all_three_different_candidates_are_reviewed(self):
        check, decision = self.protected_checker()
        decision.return_value = {"relation": "different_event", "matched_index": -1, "reason": "별도 행사"}
        self.assertEqual(check("신규 행사", ["가", "나", "다"]), "중복 아님")
        self.assertEqual(decision.call_count, 3)

    def test_later_candidate_failure_is_not_reported_as_a_complete_review(self):
        check, decision = self.protected_checker()
        decision.side_effect = [{"relation": "different_event", "matched_index": -1}, RuntimeError("API unavailable")]
        with self.assertRaises(RuntimeError):
            check("신규 행사", ["가", "나"])
        self.assertEqual(decision.call_count, 2)

    def test_same_scholarship_title_keeps_different_department_submission(self):
        check,decision=self.protected_checker();title='2027년도 한국지도자육성장학재단 장학생 선발 안내'
        context={'new_notice':{'source_url':'https://school.invalid/department-b'},'recent_notices':[{'title':title,'source_url':'https://school.invalid/department-a'}]}
        self.assertEqual(check(title,[title],context_provider=lambda _:context),'중복 아님')
        decision.assert_not_called()

    def test_retry_of_same_department_source_and_title_is_duplicate(self):
        check,decision=self.protected_checker();title='2026학년도 치의학과 학위청구논문 발표 계획'
        context={'new_notice':{'source_url':'https://school.invalid/same'},'recent_notices':[{'title':title,'source_url':'https://school.invalid/same'}]}
        self.assertEqual(check(title,[title],context_provider=lambda _:context),'중복')
        decision.assert_not_called()

    def test_ai_learning_assistant_lecture_is_not_assistant_recruitment(self):
        self.assertFalse(requires_department_evidence('학습전략특강(나만의 AI 학습 조교 설계하기)'))
        self.assertTrue(requires_department_evidence('2027학년도 교육조교 모집 안내'))

    def test_title_heuristic_cannot_bypass_department_scholarship_check(self):
        from together.notices import history
        title='2027년도 한국지도자육성장학재단 장학생 선발 안내'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'titles.txt';path.write_text(datetime.now().strftime('%Y-%m-%d %H:%M:%S')+'$'+title+'\n',encoding='utf-8')
            with patch.object(history,'check_title_similarity',return_value='중복 아님') as review:
                self.assertEqual(history.is_recent_title_duplicate(title,str(path)),'중복 아님')
            review.assert_called_once()
