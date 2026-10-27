from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import tempfile, types, unittest
from unittest.mock import MagicMock, patch
from together.notices.intelligence.enrichment import (
    apply_enrichment,
    PublishedNoticeStore,
)
from together.notices.intelligence.events import Notice
from together.notices.intelligence.post_editor import (
    PostEditError,
    PostEditRestoredError,
)


class EnrichmentRollback(unittest.TestCase):
    def exercise(self, error, expected_exception=None):
        with tempfile.TemporaryDirectory() as temp:
            current = {
                "signature": "original-signature",
                "subject": "제목",
                "body_html": "<p>기존 본문</p>",
                "files": [],
            }
            record = {
                "article_url": "https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2",
                "signature": current["signature"],
                "notice": asdict(
                    Notice(
                        "2026 캠프", datetime.now().isoformat(), body_text="기존 안내"
                    )
                ),
                "board": "교육/특강/프로그램",
                "sources": ["https://example.invalid/old"],
                "departments": ["기계"],
                "updates": 0,
            }
            store = PublishedNoticeStore(temp)
            store.put(record)
            ann = types.SimpleNamespace(
                title="2026 캠프",
                url="https://example.invalid/new",
                content_html="<p>추가 안내</p>",
                content_text="추가 안내 내용",
                files=[],
                metadata={},
            )
            with (
                patch(
                    "together.notices.intelligence.enrichment.PlatoPostEditor"
                ) as factory,
                patch(
                    "together.notices.intelligence.enrichment.compare_notices",
                    return_value={"relation": "same_event"},
                ),
                patch(
                    "together.notices.intelligence.enrichment.adds_information",
                    return_value=True,
                ),
            ):
                factory.return_value.read.return_value = current
                factory.return_value.update.side_effect = error
                if expected_exception:
                    with self.assertRaises(expected_exception):
                        apply_enrichment(
                            types.SimpleNamespace(driver=MagicMock()),
                            record,
                            ann,
                            "기계",
                            temp,
                        )
                    self.assertNotIn("ignored_sources", store.load()[0])
                else:
                    self.assertIs(
                        apply_enrichment(
                            types.SimpleNamespace(driver=MagicMock()),
                            record,
                            ann,
                            "기계",
                            temp,
                        ),
                        False,
                    )
                    after = store.load()[0]
                    self.assertEqual(after["signature"], current["signature"])
                    self.assertIn(ann.url, after["ignored_sources"])
                    self.assertEqual(after["notice"], record["notice"])

    def test_only_verified_restore_can_continue_the_cycle(self):
        self.exercise(
            PostEditRestoredError("restored", {"signature": "original-signature"})
        )

    def test_unknown_or_failed_rollback_still_stops_processing(self):
        self.exercise(PostEditError("Restore failed"), PostEditError)

    def test_different_restored_signature_is_not_accepted(self):
        self.exercise(
            PostEditRestoredError("restored", {"signature": "different"}), PostEditError
        )
