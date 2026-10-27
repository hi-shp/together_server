"""수정 대상 보호, 파일 보존, 후보 선택과 기존 알림 흐름을 검사한다."""

from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import contextlib
import io

os.environ["NOTICE_ENRICHMENT_MODE"] = "off"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.notices.intelligence.events import Notice
from together.notices.intelligence.enrichment import (
    PublishedNoticeStore,
    adds_information,
    enriched_subject,
    merged_body,
    apply_enrichment,
    register_post,
)
from together.notices.intelligence.post_editor import (
    PlatoPostEditor,
    PostEditError,
    snapshot_signature,
    validate_article_url,
)

URL = "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1885169&bwid=2474380"


def snapshot(subject="원래 제목", body="<p>원래 본문</p>", files=None):
    value = {
        "article_url": URL,
        "subject": subject,
        "body_html": body,
        "files": files or [],
        "notice": False,
    }
    value["signature"] = snapshot_signature(value)
    return value


class PostEditorProtection(unittest.TestCase):
    def test_external_and_malformed_targets_are_rejected(self):
        for url in (
            "http://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2",
            "https://other.invalid/mod/ubboard/article.php?id=1&bwid=2",
            "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2&bwid=3",
        ):
            with self.assertRaises(PostEditError):
                validate_article_url(url)

    def test_manual_changes_block_all_field_and_file_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            editor = PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()), temp)
            current = snapshot(subject="사람이 수정한 제목")
            current["backup_dir"] = temp
            editor.read = MagicMock(return_value=current)
            editor._set_fields = MagicMock()
            editor._save = MagicMock()
            editor._upload = MagicMock()
            with self.assertRaises(PostEditError):
                editor.update(URL, "새 제목", "<p>본문</p>", expected_signature="old")
            editor._set_fields.assert_not_called()
            editor._save.assert_not_called()
            editor._upload.assert_not_called()

    def test_same_name_never_overwrites_an_existing_attachment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "신청서.pdf"
            path.write_bytes(b"new")
            before = snapshot(files=[{"name": path.name, "sha256": "old"}])
            before["backup_dir"] = temp
            editor = PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()), temp)
            editor.read = MagicMock(return_value=before)
            editor._upload = MagicMock()
            editor._save = MagicMock()
            with self.assertRaises(PostEditError):
                editor.update(URL, "제목", "<p>본문</p>", [path])
            editor._upload.assert_not_called()
            editor._save.assert_not_called()

    def test_save_requires_original_and_new_file_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "new.txt"
            path.write_bytes(b"new")
            before = snapshot(files=[{"name": "old.txt", "sha256": "old"}])
            before["backup_dir"] = temp
            after = snapshot(
                "제목",
                "<p>본문</p>",
                files=[
                    {"name": path.name, "sha256": hashlib.sha256(b"new").hexdigest()}
                ],
            )
            editor = PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()), temp)
            editor.read = MagicMock(return_value=before)
            editor._upload = MagicMock()
            editor._set_fields = MagicMock()
            editor._save = MagicMock(return_value=after)
            editor.restore = MagicMock()
            with self.assertRaises(PostEditError):
                editor.update(URL, "제목", "<p>본문</p>", [path])
            editor.restore.assert_called_once()

    def test_restore_does_not_overwrite_an_intervening_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            editor = PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()), temp)
            editor.read = MagicMock(return_value=snapshot(subject="새로운 수동 수정"))
            editor._delete_added = MagicMock()
            editor._set_fields = MagicMock()
            with self.assertRaises(PostEditError):
                editor.restore(
                    {
                        "before": snapshot(),
                        "after": snapshot(subject="보강"),
                        "added_files": [],
                    }
                )
            editor._delete_added.assert_not_called()
            editor._set_fields.assert_not_called()


class CandidateAndContentTests(unittest.TestCase):
    def notice(
        self, title="2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집", **kwargs
    ):
        return Notice(
            title=title,
            observed_at=datetime.now().isoformat(),
            source_url="https://example.invalid/old",
            department="기계",
            **kwargs,
        )

    def test_different_deadlines_in_body_cannot_be_merged(self):
        self.assertFalse(
            adds_information(
                self.notice(body_text="접수기간 10/20"),
                self.notice(
                    body_text="접수기간 10/30",
                    attachment_urls=["https://example.invalid/a"],
                ),
            )
        )

    def test_representative_body_replaces_previous_copy(self):
        old = self.notice(body_text="모집 안내")
        ann = types.SimpleNamespace(
            url="https://example.invalid/new",
            content_text="신청방법: 온라인",
            content_html="<p>신청방법: 온라인</p>",
        )
        body = merged_body("<p>기존 연락처 051-000-0000</p>", ann, old)
        self.assertNotIn("기존 연락처 051-000-0000", body)
        self.assertNotIn("추가 안내", body)
        self.assertIn("신청방법: 온라인", body)
        self.assertIn(ann.url, body)

    def test_star_and_department_markers_are_preserved(self):
        self.assertEqual(
            enriched_subject(
                "⭐원래 제목", "2026 캠프 모집(~10/30)", "교육/특강/프로그램"
            ),
            "⭐2026 캠프 모집",
        )
        self.assertEqual(
            enriched_subject("[기계] 원래 제목", "새 장학 안내", "장학금"),
            "[기계] 새 장학 안내",
        )

    def test_other_year_and_ambiguous_multiple_posts_are_not_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PublishedNoticeStore(temp)
            old = self.notice(body_text="모집 안내")
            record = {
                "article_url": URL,
                "signature": "a",
                "notice": asdict(old),
                "board": "교육/특강/프로그램",
                "sources": [old.source_url],
                "departments": ["기계"],
                "updates": 0,
                "file_hashes": {},
            }
            store.put(record)
            ann = types.SimpleNamespace(
                title="2025년 제3기 해양정보 로봇 개발 캠프 참가자 모집",
                url="https://example.invalid/new",
                content_text="신청방법: 온라인",
                metadata={
                    "attachment_links": [{"url": "https://example.invalid/file"}]
                },
            )
            self.assertIsNone(store.find(ann, "기계"))
            ann.title = old.title
            self.assertIsNotNone(store.find(ann, "기계"))
            store.put(dict(record, article_url=URL.replace("2474380", "2474381")))
            self.assertIsNone(store.find(ann, "기계"))

    def test_registration_store_does_not_retain_old_rows_indefinitely(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PublishedNoticeStore(temp)
            old = self.notice()
            old.observed_at = (datetime.now() - timedelta(days=15)).isoformat()
            store.put({"article_url": URL, "notice": asdict(old)})
            self.assertEqual(store.load(), [])

    def test_live_manual_change_prevents_enrichment(self):
        with tempfile.TemporaryDirectory() as temp:
            service = types.SimpleNamespace(driver=MagicMock())
            with patch(
                "together.notices.intelligence.enrichment.PlatoPostEditor"
            ) as factory:
                factory.return_value.read.return_value = snapshot(subject="수동 수정")
                record = {
                    "article_url": URL,
                    "signature": "old",
                    "notice": asdict(self.notice()),
                    "sources": [],
                }
                self.assertIsNone(apply_enrichment(service, record, None, "기계", temp))
                factory.return_value.update.assert_not_called()
                self.assertTrue(PublishedNoticeStore(temp).load()[0]["disabled"])

    def test_wrong_article_receipt_never_registers_a_post_for_future_edits(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"NOTICE_ENRICHMENT_MODE": "enabled"}),
        ):
            ann = types.SimpleNamespace(
                title="공지", url="https://example.invalid/source"
            )
            with patch(
                "together.notices.intelligence.enrichment.PlatoPostEditor"
            ) as factory:
                factory.return_value.read.return_value = snapshot(subject="다른 게시물")
                register_post(
                    types.SimpleNamespace(driver=MagicMock()),
                    ann,
                    "기계",
                    {
                        "article_url": URL,
                        "subject": "기대한 제목",
                        "source_url": ann.url,
                    },
                    temp,
                )
            self.assertEqual(PublishedNoticeStore(temp).load(), [])


class MainEnrichmentFlow(unittest.TestCase):
    def run_case(self, outcome, category):
        import main

        service = MagicMock()
        service.update_participants.return_value = 1
        service.download_survey.return_value = 1
        service.write_notices.return_value = []
        ann = types.SimpleNamespace(
            title="2026 해양 프로그램 모집",
            content_text="참가자 모집",
            content_html="<p>참가자 모집</p>",
            url="https://example.invalid/new",
            metadata={},
        )
        full = types.SimpleNamespace(
            title=ann.title,
            content_text=ann.content_text,
            content_html=ann.content_html,
            url=ann.url,
            files=[],
            notice_board_name="",
        )
        page = types.SimpleNamespace(
            source_url="csv-key",
            page_url="https://example.invalid/list",
            number=5,
            department="기계",
        )

        class Afternoon(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, 13)

        with tempfile.TemporaryDirectory() as temp:
            previous = Path.cwd()
            os.chdir(temp)
            try:
                with (
                    patch.object(main, "NoticeUpdater"),
                    patch.object(
                        main,
                        "PageUrlManager",
                        return_value=types.SimpleNamespace(announcement_pages=[page]),
                    ),
                    patch.object(main, "get_anns_url", return_value=([ann.url], 6)),
                    patch.object(main, "crawl_ann_partial", return_value=ann),
                    patch.object(main, "crawl_ann", return_value=full),
                    patch.object(
                        main,
                        "find_target",
                        return_value={"board": "교육/특강/프로그램"},
                    ),
                    patch.object(main, "apply_enrichment", return_value=outcome),
                    patch.object(
                        main, "is_recent_title_duplicate", return_value="중복 아님"
                    ),
                    patch.object(
                        main, "categorize_announcement", return_value=category
                    ) as classify,
                    patch.object(main, "recent_title", return_value=[]),
                    patch.object(main, "save_title"),
                    patch.object(main, "save_only_title"),
                    patch.object(main, "update_csv_with_announcement_numbers"),
                    patch.object(main, "notify"),
                    patch.object(main, "observe_notice"),
                    patch.object(main, "datetime", Afternoon),
                    patch.object(main.os.path, "expanduser", return_value=temp),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    main.run_cycle(service)
                    classification_calls = classify.call_count
            finally:
                os.chdir(previous)
        return service, classification_calls, full

    def test_enrichment_replaces_new_post_but_keeps_department_notifications(self):
        service, calls, _ = self.run_case(True, "교육/특강/프로그램")
        service.write_notices.assert_not_called()
        self.assertEqual(calls, 0)
        service.send_message.assert_called_once()
        service.send_email.assert_called_once()

    def test_manual_change_keeps_original_new_post_and_other_board_behavior(self):
        service, calls, full = self.run_case(None, "해당없음")
        service.write_notices.assert_called_once()
        self.assertEqual(calls, 1)
        self.assertEqual(full.notice_board_name, "기타 공지사항")
        service.send_message.assert_called_once()
        service.send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
