"""수정 전 수집 함수와 대표 게시판 양식의 결과를 대조한다."""

import ast
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

sys.modules.setdefault(
    "together.integrations.openai_client",
    types.SimpleNamespace(answer_gpt=None, check_title_similarity=None),
)
from together.crawler import announcements as crawler


class LegacyBranchCompatibility(unittest.TestCase):
    def test_existing_board_branches_keep_all_original_candidates(self):
        base = Path(
            os.getenv("LEGACY_TEST_GIT_ROOT") or Path(__file__).resolve().parents[1]
        )
        original = subprocess.run(
            [
                "git",
                "show",
                "50d0d258f1d867a39b5ef58898652746ca1f86ee:crawl_announcement.py",
            ],
            cwd=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if original.returncode:
            self.skipTest(
                "Original Git history is unavailable in a source-only staging folder"
            )
        tree = ast.parse(original.stdout)
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_anns_url"
        )
        fixtures = [
            (
                "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=2",
                '<tr><td class="num">9</td><td class="subject"><a href="?board_seq=1511715&mode=view">2026학년도 겨울계절수업 및 도약수업 운영 안내</a></td></tr>',
            ),
            (
                "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1",
                '<tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?board_seq=1">첫 새 고정</a></td></tr><tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?board_seq=2">이전 고정</a></td></tr><tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?board_seq=3">기준 아래 옛 고정</a></td></tr><tr><td class="num">12</td><td class="subject"><a href="?board_seq=4">새 일반</a></td></tr>',
            ),
            (
                "https://old.example/list",
                '<tr><td class="_artclTdNum">12</td><td class="_artclTdTitle"><a class="artclLinkView" href="/view/12">새 글</a></td></tr>',
            ),
            (
                "https://old.example/new/sub05/sub01_01.php",
                '<tr><td class="number">12</td><td><a href="javascript:goDetail(123)">새 글</a></td></tr>',
            ),
            (
                "https://old.example/new/sub05/sub01_02.php",
                '<tr><td class="number">12</td><td><a href="javascript:goDetail(123)">새 글</a></td></tr>',
            ),
            (
                "https://old.example/new/sub05/sub01_05.php",
                '<tr><td class="number">12</td><td><a href="javascript:goDetail(123)">새 글</a></td></tr>',
            ),
            (
                "https://old.example/list",
                '<tr><td class="td-num">12</td><td class="td-title"><a href="/view/12">새 글</a></td></tr>',
            ),
            (
                "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1",
                '<tr><td class="num"><img alt="공지"></td><td class="subject"><a href="?board_seq=123&mode=view">새 고정</a></td></tr><tr><td class="num">12</td><td class="subject"><a href="?board_seq=124&mode=view">새 일반</a></td></tr>',
            ),
        ]
        for url, body in fixtures:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old = Path.cwd()
                os.chdir(root)
                response = types.SimpleNamespace(
                    text="<table><tbody>" + body + "</tbody></table>",
                    url=url,
                    raise_for_status=lambda: None,
                )
                page = types.SimpleNamespace(
                    page_url=url,
                    default_url="https://old.example/",
                    number=10,
                    department="검증",
                )
                namespace = {
                    "time": types.SimpleNamespace(
                        sleep=lambda *a: None, time=lambda: 0
                    ),
                    "requests": types.SimpleNamespace(get=lambda *a, **k: response),
                    "BeautifulSoup": BeautifulSoup,
                    "recent_title": lambda: [],
                    "urljoin": urljoin,
                    "urlparse": urlparse,
                    "re": re,
                    "os": os,
                    "print": lambda *a, **k: None,
                }
                exec(
                    compile(
                        ast.Module(body=[function], type_ignores=[]),
                        "<original board collector>",
                        "exec",
                    ),
                    namespace,
                )
                try:
                    (root / "yesterday.txt").write_text(
                        "이전 일반\n이전 고정\n", encoding="utf-8"
                    )
                    legacy_urls, legacy_number = namespace["get_anns_url"](page, 1)
                    (root / "yesterday.txt").write_text(
                        "이전 일반\n이전 고정\n", encoding="utf-8"
                    )
                    with (
                        patch.object(crawler, "BASE_DIR", root),
                        patch.object(crawler, "http_get", return_value=response),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        current_urls, current_number = crawler.get_anns_url(page, 1)
                    self.assertEqual(
                        legacy_urls,
                        current_urls,
                        (legacy_urls, current_urls),
                    )
                    self.assertGreaterEqual(current_number, legacy_number)
                finally:
                    os.chdir(old)
