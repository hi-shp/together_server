"""제목 전체의 대괄호와 판단 당시 본문 연결을 검사한다."""

import ast
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import MagicMock
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
                    "relation": "needs_context",
                    "matched_index": -1,
                    "reason": "본문 확인 필요",
                },
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
        self.assertEqual(decision.call_count, 2)
        payload = json.loads(
            decision.call_args_list[1].args[1]["messages"][1]["content"]
        )
        self.assertEqual(payload["new_notice"]["body_excerpt"], "이번 본문")
        self.assertIn("GPT 본문 검토", duplicate_review.get())
        duplicate_review.set("")
        duplicate_review_reason.set("")
