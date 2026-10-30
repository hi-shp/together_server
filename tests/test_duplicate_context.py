"""제목 전체의 대괄호와 판단 당시 본문 연결을 검사한다."""

import ast
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from together.notices.intelligence.text import requires_department_evidence, normalize_title
from together.notices.history import comparison_title, calculate_similarity, candidate_title_similarities
from together.notices.intelligence.context import duplicate_context, department_conditions_differ
from together.notices.intelligence.prompts import duplicate_request
from together.operations.events import duplicate_review, duplicate_review_reason


class ContextTests(unittest.TestCase):
    def test_event_name_inside_brackets_with_generic_tail_is_preserved(self):
        title = "[ 2026 Arise Together : 문제해결 프로젝트 ] 모집 안내"
        self.assertIn("arisetogether", comparison_title(title))
        candidates = ["[홍보·안내] SSAFY 17기 모집 안내", "[현장실습지원센터] 26겨울계절학기 참여학생 모집 안내", "[글로컬 3-2-2] 「2026 Arise Together_ 문제해결 프로젝트」안내"]
        ranked = candidate_title_similarities(title, candidates)
        self.assertEqual(ranked[0][1], comparison_title(candidates[-1]))

    def test_same_named_cohort_is_retrieved_before_shorter_information_sessions(self):
        title = "삼성청년SW·AI아카데미(SSAFY) 사무국 - SSAFY AI인재 양성 17기 교육생 모집"
        previous = "[삼성청년SW,AI아카데미] AI역량부터 취업까지 한 번에 준비할 기회! 삼성이 만든 AI교육 SSAFY! 17기 모집 시작(~11/9)"
        candidates = ["삼성청년 SW·AI 아카데미(SSAFY) 설명회 안내", "삼성청년 SW·AI 아카데미(SSAFY) 설명회 안내 및 홍보", "삼성청년 SW·AI 아카데미(SSAFY) 설명회 일정 안내", previous]
        self.assertIn(comparison_title(previous), [value for _, value in candidate_title_similarities(title, candidates)])

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
            "normalize_title": normalize_title,
            "occurrence_conflict": lambda a, b: None,
            "duplicate_request": duplicate_request,
            "_decision": decision,
            "DUPLICATE_MODEL": "test",
            "BASE_DIR": "test",
            "ApiAccounting": MagicMock(return_value=accounting),
            "duplicate_review": duplicate_review,
            "duplicate_review_reason": duplicate_review_reason,
            "department_conditions_differ": department_conditions_differ,
            "re": re,
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
        scope={'requires_department_evidence':requires_department_evidence,'normalize_title':normalize_title,'occurrence_conflict':lambda a,b:None,'duplicate_request':duplicate_request,'department_conditions_differ':department_conditions_differ,'_decision':decision,'DUPLICATE_MODEL':'test','BASE_DIR':'test','ApiAccounting':MagicMock(),'duplicate_review':duplicate_review,'duplicate_review_reason':duplicate_review_reason,'re':re}
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

    def test_common_internship_uses_both_bodies_instead_of_skipping_every_department(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절학기 현장실습학기제 학생 모집 및 수강신청 안내"
        body = "현장실습지원센터 공통 운영 일정 12월 23일부터 2월 28일까지. 학생 모집 11월 20일부터 24일까지. " * 3
        context = {"new_notice": {"source_url": "https://school.invalid/new", "body_excerpt": body}, "recent_notices": [{"title": title, "source_url": "https://school.invalid/old", "body_excerpt": body}]}
        decision.return_value = {"relation": "same_event", "matched_index": 0, "reason": "동일 공통 운영 일정과 신청 조건"}
        self.assertEqual(check(title, [title], context_provider=lambda _: context), "중복")
        decision.assert_called_once()
        payload = json.loads(decision.call_args.args[1]["messages"][1]["content"])
        self.assertEqual(payload["candidates"][0]["body_excerpt"], body)

    def test_common_internship_without_both_bodies_does_not_merge(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절학기 현장실습학기제 학생 모집 및 수강신청 안내"
        self.assertEqual(check(title, [title]), "중복 아님")
        decision.assert_not_called()

    def test_common_exchange_notice_uses_both_bodies_instead_of_skipping_departments(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절수업 교류 수학 안내 [한양대, 서울시립대, 고려대]"
        body = (
            "한양대학교 서울시립대학교 고려대학교 겨울계절수업 교류 수학을 안내합니다. "
            "추천기한과 수업기간 및 수학신청자 정보 입력 절차는 다음과 같습니다. "
        ) * 4
        context = {
            "new_notice": {
                "source_url": "https://department-a.invalid/notice",
                "department": "실내환경디자인학과",
                "body_excerpt": body,
            },
            "recent_notices": [
                {
                    "title": title,
                    "source_url": "https://department-b.invalid/notice",
                    "department": "물리학과(학부)",
                    "body_excerpt": body,
                }
            ],
        }
        decision.return_value = {
            "relation": "same_event",
            "matched_index": 0,
            "reason": "같은 대학·추천기한·수업기간과 신청 절차",
        }
        self.assertEqual(
            check(title, [title], context_provider=lambda _: context), "중복"
        )
        decision.assert_called_once()

    def test_common_exchange_notice_without_both_bodies_remains_separate(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절수업 교류 수학 안내"
        self.assertEqual(check(title, [title]), "중복 아님")
        decision.assert_not_called()

    def test_department_internship_conditions_can_remain_an_additional_notice(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절학기 현장실습학기제 학생 모집 및 수강신청 안내"
        context = {"new_notice": {"body_excerpt": "학과별 제출 기한과 학점 인정 조건이 있습니다. " * 10}, "recent_notices": [{"title": title, "body_excerpt": "현장실습지원센터 전체 학생 공통 운영 일정을 안내합니다. " * 10}]}
        decision.return_value = {"relation": "related_update", "matched_index": -1, "reason": "학과별 인정 학점과 제출 기한 추가"}
        self.assertEqual(check(title, [title], context_provider=lambda _: context), "중복 아님")
        self.assertIn("변경·추가 안내", duplicate_review.get())

    def test_department_specific_credit_conditions_are_preserved_even_if_model_would_merge(self):
        check, decision = self.protected_checker()
        title = "2026학년도 겨울계절학기 현장실습학기제 학생 모집 및 수강신청 안내"
        body = "현장실습지원센터 겨울계절학기 운영 일정과 수강신청 절차를 안내합니다. " * 4
        context = {"new_notice": {"department": "경영학과(학부)", "body_excerpt": "경영학과의 현장실습 학점인정은 최대 일반선택 6학점입니다. 전공선택 학점 인정 불가. " + body}, "recent_notices": [{"title": title, "department": "나노에너지공학과", "body_excerpt": body}]}
        decision.return_value = {"relation": "same_event", "matched_index": 0, "reason": "공통 일정 일치"}
        self.assertEqual(check(title, [title], context_provider=lambda _: context), "중복 아님")
        decision.assert_not_called()
        self.assertIn("본문 조건 대조", duplicate_review.get())

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
