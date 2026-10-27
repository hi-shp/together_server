"""최적화 전후의 중복 판정과 게시 작업 순서를 비교한다."""

import contextlib
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import gc
import io
import os
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules["together.integrations.openai_client"] = types.SimpleNamespace(
    answer_gpt=MagicMock(), check_title_similarity=MagicMock()
)

from together.notices import history as checker
import main


def reference_top(new_title, titles):
    scores = [
        (SequenceMatcher(None, new_title, title).ratio(), title) for title in titles
    ]
    scores.sort(key=lambda pair: pair[0], reverse=True)
    return scores[:3]


class SimilarityCompatibility(unittest.TestCase):
    def compare(self, query, titles):
        self.assertEqual(
            checker._top_title_similarities(query, titles), reference_top(query, titles)
        )

    def test_empty_and_short_histories(self):
        for titles in ([], [""], ["a", "b"], ["a", "b", "c"]):
            self.compare("a", titles)

    def test_equal_scores_keep_original_order(self):
        titles = ["aa", "ab", "ac", "ad", "aa", "ae"]
        self.compare("a", titles)
        self.assertEqual(
            [title for _, title in checker._top_title_similarities("a", titles)],
            titles[:3],
        )

    def test_unicode_long_repetition_and_autojunk(self):
        titles = [
            "학부 공지 안내",
            "🎓 모집 안내",
            "a" * 210 + "b",
            "a" * 210 + "c",
            "",
            "가 안내",
            "가 안내",
        ]
        for query in titles:
            self.compare(query, titles + titles[::-1])

    def test_seeded_randomized_histories(self):
        rng = random.Random(20261026)
        alphabet = "학과공지모집안내abc XYZ123[]😀"
        for _ in range(250):
            titles = [
                "".join(rng.choices(alphabet, k=rng.randrange(0, 90)))
                for _ in range(rng.randrange(0, 70))
            ]
            query = "".join(rng.choices(alphabet, k=rng.randrange(0, 90)))
            self.compare(query, titles)

    def test_gpt_arguments_and_duplicate_threshold_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "titles.txt"
            now = datetime.now()
            titles = [
                "2026 공모전 참가 안내",
                "신입 채용 안내",
                "[학부] 장학금 신청 안내",
                "다른 제목",
                "동일한 점수",
            ]
            target.write_text(
                "".join(
                    f"{now - timedelta(seconds=i):%Y-%m-%d %H:%M:%S}${title}\n"
                    for i, title in enumerate(titles)
                ),
                encoding="utf-8",
            )
            for query in titles + ["처음 보는 공지사항"]:
                snapshots = []
                for selector in (reference_top, checker._top_title_similarities):
                    output = io.StringIO()
                    with (
                        patch.object(checker, "_top_title_similarities", selector),
                        patch.object(
                            checker, "check_title_similarity", return_value="중복 아님"
                        ) as gpt,
                        contextlib.redirect_stdout(output),
                    ):
                        result = checker.is_recent_title_duplicate(query, str(target))
                    snapshots.append((result, output.getvalue(), gpt.call_args_list))
                self.assertEqual(*snapshots)


class TimestampCompatibility(unittest.TestCase):
    def test_valid_timestamps_match_the_original_parser(self):
        rng = random.Random(20261026)
        origin = datetime(2000, 1, 1)
        values = [
            (origin + timedelta(seconds=rng.randrange(0, 50 * 365 * 86400))).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            for _ in range(300)
        ]
        values.extend(
            [
                "2026-1-2 3:4:5",
                "2024-02-29 00:00:00",
                "0001-01-01 00:00:00",
                "9999-12-31 23:59:59",
            ]
        )
        for value in values:
            self.assertEqual(
                checker._parse_title_timestamp(value),
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S"),
            )

    def test_invalid_timestamps_keep_the_original_errors(self):
        for value in [
            "",
            "2026-02-30 12:00:00",
            "2026-13-01 00:00:00",
            "2026-01-01 24:00:00",
            "2026-01-01 00:00:60",
            "0000-01-01 00:00:00",
            "2026-01-01T00:00:00",
            "2026-01-01 00:00:00+00:00",
            "2026-01-01 00:00:00 extra",
        ]:
            with self.assertRaises(ValueError) as original:
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            with self.assertRaises(ValueError) as optimized:
                checker._parse_title_timestamp(value)
            self.assertEqual(str(original.exception), str(optimized.exception))


class CycleCompatibility(unittest.TestCase):
    def test_posting_and_notifications_keep_order_without_retaining_old_bodies(self):
        events = []
        references = []
        partials = {
            f"https://example.invalid/{i}": types.SimpleNamespace(
                title=f"공지 {i}",
                content_text="내용",
                content_html="<p>내용</p>",
                url=f"https://example.invalid/{i}",
            )
            for i in (1, 2)
        }
        urls = list(partials)

        class FullNotice:
            pass

        def full_notice(url, category):
            notice = FullNotice()
            notice.title = partials[url].title
            notice.content_text = "내용"
            notice.content_html = "<p>내용</p>"
            notice.url = url
            notice.files = []
            references.append(weakref.ref(notice))
            return notice

        test = self

        class Service:
            def update_participants(self):
                return 12

            def download_survey(self):
                return 2

            def write_notices(self, course, notices, department):
                if len(references) > 1:
                    gc.collect()
                    test.assertIsNone(
                        references[0](), "지난 공지 본문이 불필요하게 남아 있습니다."
                    )
                events.append(("post", notices[0].title, department))

            def move_to_course(self, course):
                events.append(("course",))

            def send_message(self, title, url, department):
                events.append(("message", title, department))

            def send_email(self, title, url, html, department):
                events.append(("email", title, department))

            def remove_stars(self, course, date):
                events.append(("stars",))

        class Afternoon(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, 13)

        with tempfile.TemporaryDirectory() as temp:
            old = Path.cwd()
            os.chdir(temp)
            page = types.SimpleNamespace(
                page_url="https://example.invalid/list",
                source_url="original-csv-key",
                number=10,
                department="테스트학과",
            )
            with (
                patch.object(main, "NoticeUpdater"),
                patch.object(
                    main,
                    "PageUrlManager",
                    return_value=types.SimpleNamespace(announcement_pages=[page]),
                ),
                patch.object(main, "get_anns_url", return_value=(urls, 12)),
                patch.object(
                    main, "crawl_ann_partial", side_effect=lambda url: partials[url]
                ),
                patch.object(main, "crawl_ann", side_effect=full_notice),
                patch.object(
                    main, "is_recent_title_duplicate", return_value="중복 아님"
                ),
                patch.object(main, "categorize_announcement", return_value="해당없음"),
                patch.object(main, "recent_title", return_value=[]),
                patch.object(main, "save_title"),
                patch.object(main, "save_only_title"),
                patch.object(main, "notify"),
                patch.object(main, "datetime", Afternoon),
                patch.object(main, "update_csv_with_announcement_numbers") as update,
                patch.object(main.os.path, "expanduser", return_value=temp),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                try:
                    main.run_cycle(Service())
                finally:
                    os.chdir(old)
            update.assert_called_once_with(
                {"original-csv-key": 12}, os.getenv("PAGE_NAME")
            )
        self.assertEqual(
            events,
            [
                ("post", "공지 1", "테스트학과"),
                ("course",),
                ("message", "공지 1", "테스트학과"),
                ("email", "공지 1", "테스트학과"),
                ("post", "공지 2", "테스트학과"),
                ("course",),
                ("message", "공지 2", "테스트학과"),
                ("email", "공지 2", "테스트학과"),
            ],
        )
        self.assertTrue(all(reference() is None for reference in references))


if __name__ == "__main__":
    unittest.main()
