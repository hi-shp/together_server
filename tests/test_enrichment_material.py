from dataclasses import asdict
from datetime import datetime
import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from together.notices.intelligence.enrichment import apply_enrichment, PublishedNoticeStore
from together.notices.intelligence.events import Notice
from together.notices.intelligence.enrichment_quality import body_gain, profile

TABLE = '<table><tr><th>시간</th><th>프로그램</th></tr><tr><td>13:00</td><td>지역교육</td></tr></table>'
BODY = '<p>2026년 IRB 지역교육 안내. 일시 11/6 13:00 부산대학교 건설관 301호. 신청방법 홈페이지 접수.</p>'


class MaterialEnrichment(unittest.TestCase):
    def test_same_information_and_small_wording_changes_are_not_improvement(self):
        old = BODY + TABLE
        self.assertEqual(body_gain(old, old.replace('안내.', '개최합니다.')), [])
        self.assertEqual(body_gain(old * 3, old), [])
        self.assertEqual(body_gain(old.replace('13:00', '13:00(?10)'), old.replace('13:00', "13:00('10)")), [])

    def test_table_and_image_are_material_but_icons_and_url_changes_are_not(self):
        self.assertIn('table_added', body_gain(BODY + '<p>시간 프로그램 13:00 지역교육</p>', BODY + TABLE))
        self.assertIn('image_added', body_gain(BODY, BODY + '<img src="https://a.invalid/poster.png">'))
        self.assertEqual(body_gain(BODY + '<img src="https://a.invalid/one.png">', BODY + '<img src="https://a.invalid/two.png">'), [])
        self.assertEqual(profile('<img src="/ico_file.gif"><img src="/pixel.gif" width="1">')['images'], 0)
        self.assertEqual(body_gain(BODY + '<img src="https://a.invalid/old.png">', BODY + '<img src="https://a.invalid/new1.png"><img src="https://a.invalid/new2.png">'), [])

    def test_replacement_keeps_dates_contact_counts_tables_and_images(self):
        for old, new in ((BODY, BODY.replace('11/6', '11/7')), (BODY + '<p>051-123-4567</p>', BODY), (BODY + '<p>20명</p>', BODY), (BODY + TABLE, BODY)):
            self.assertEqual(body_gain(old, new + '<img src="https://a.invalid/new.png">'), [])

    def test_substantial_broken_text_repair_is_material(self):
        self.assertIn('broken_text_repaired', body_gain(BODY + '<p>참가 안내 ???</p>', BODY + '<p>참가 안내</p>'))

    def exercise_file(self, different):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '안내.pdf'
            path.write_bytes(b'new' if different else b'old')
            old_hash = hashlib.sha256(b'old').hexdigest()
            current = {'signature': 'same', 'subject': '기존 제목', 'body_html': BODY + TABLE, 'files': [{'name': path.name, 'sha256': old_hash}]}
            old = Notice('2026 IRB 지역교육', datetime.now().isoformat(), source_url='https://a.invalid/old', body_text='지역교육', attachment_urls=['https://a.invalid/old-file'])
            record = {'article_url': 'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2', 'signature': 'same', 'notice': asdict(old), 'board': '교육/특강/프로그램', 'sources': [old.source_url], 'departments': ['기계'], 'updates': 0}
            store = PublishedNoticeStore(temp)
            store.put(record)
            ann = types.SimpleNamespace(title=old.title, url='https://a.invalid/new', content_text='지역교육', content_html=BODY, files=[str(path)], metadata={'attachment_links': [{'url': 'https://a.invalid/new-file'}]})
            with patch('together.notices.intelligence.enrichment.PlatoPostEditor') as factory, patch('together.notices.intelligence.enrichment.compare_notices', return_value={'relation': 'same_event'}), patch.dict('os.environ', {'POST_VALIDATION_MODE': 'off'}):
                editor = factory.return_value
                editor.read.return_value = current
                editor.update.return_value = {'after': dict(current, signature='updated')}
                outcome = apply_enrichment(types.SimpleNamespace(driver=MagicMock()), record, ann, '기계', temp)
                if different:
                    self.assertTrue(outcome)
                    args = editor.update.call_args.args
                    self.assertEqual(args[1], current['subject'])
                    self.assertEqual(args[2], current['body_html'])
                    self.assertEqual(len(args[3]), 1)
                else:
                    self.assertFalse(outcome)
                    editor.update.assert_not_called()
                    self.assertEqual(store.load()[0]['updates'], 0)

    def test_same_attachment_bytes_at_another_url_do_not_trigger_edit(self):
        self.exercise_file(False)

    def test_actual_new_attachment_preserves_existing_body_and_title(self):
        self.exercise_file(True)

    def test_new_image_attachment_is_excluded_without_removing_published_files(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'poster-disguised.pdf'
            image.write_bytes(b'\x89PNG\r\n\x1a\nimage')
            old = Notice('2026 IRB 지역교육', datetime.now().isoformat(), source_url='https://a.invalid/old', body_text='지역교육')
            record = {'article_url': 'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2', 'signature': 'same', 'notice': asdict(old), 'sources': [old.source_url], 'departments': ['기계'], 'updates': 0}
            PublishedNoticeStore(temp).put(record)
            ann = types.SimpleNamespace(title=old.title, url='https://a.invalid/new', content_text='지역교육', content_html=BODY, files=[str(image)], metadata={'attachment_links': [{'url': 'https://a.invalid/new-image', 'name': 'poster.png'}]})
            current = {'signature': 'same', 'subject': '기존 제목', 'body_html': BODY, 'files': [{'name': 'previous-image.png', 'sha256': 'existing'}]}
            with patch('together.notices.intelligence.enrichment.PlatoPostEditor') as factory, patch('together.notices.intelligence.enrichment.compare_notices', return_value={'relation': 'same_event'}), patch('together.notices.intelligence.enrichment.adds_information', return_value=True):
                factory.return_value.read.return_value = current
                self.assertFalse(apply_enrichment(types.SimpleNamespace(driver=MagicMock()), record, ann, '기계', temp))
                factory.return_value.update.assert_not_called()
            self.assertEqual(current['files'], [{'name': 'previous-image.png', 'sha256': 'existing'}])
