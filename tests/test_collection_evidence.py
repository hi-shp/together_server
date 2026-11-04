import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
from together.notices.intelligence.metadata import extract_metadata
from together.notices.intelligence.collection import record_collection, observe_collection


class CollectionEvidenceTests(unittest.TestCase):
    def test_parallel_identical_payload_is_one_complete_blob(self):
        from concurrent.futures import ThreadPoolExecutor
        from together.notices.intelligence.collection import _blob
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            payload={'body':'동일 원문'*1000}
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _: _blob(root,payload),range(32)))
            self.assertEqual(len(set(results)),1)
            self.assertEqual(json.loads(gzip.decompress((root/results[0][1]).read_bytes())),payload)
            self.assertEqual(list(root.rglob('*.pending')),[])

    def meta(self, html):
        return extract_metadata(BeautifulSoup(html,'html.parser'),'https://test.pusan.ac.kr/view/1',requested_url='https://test.pusan.ac.kr/old/1')

    def test_all_current_body_templates_have_separate_counts(self):
        for selector in ('class="artclView"','id="boardContents"','class="board-contents clear"','class="board-view-cont"','class="txt"'):
            with self.subTest(selector=selector):
                value=self.meta('<img src="logo.png"><div class="vtitle-winfo"><span class="txt">작성일자 2026-11-03</span></div><div '+selector+'><table><tr><td>대상: 학생</td></tr></table><img src="poster.png"></div>')
                self.assertEqual(value['inline_image_count'],1)
                self.assertEqual(value['table_count'],1)
                self.assertNotEqual(value['layout'],'unknown')
                self.assertEqual(value['requested_url'],'https://test.pusan.ac.kr/old/1')

    def test_body_time_and_definition_are_not_creation(self):
        result=self.meta('<div class="artclView"><time datetime="2026-11-10">행사</time><dl><dt>작성일</dt><dd>2026-11-12</dd></dl></div>')
        self.assertIsNone(result['publication_time'])

    def test_table_date_and_korean_date(self):
        value=self.meta('<table><tr><th>작성일</th><td>2026년 11월 3일</td></tr></table><div id="boardContents">본문</div>')
        self.assertEqual(value['publication_time'],'2026-11-03')

    def test_onestop_hidden_accessibility_date_label(self):
        value=self.meta('<div class="board-view-title"><p class="board-info"><span><span class="sr-only">작성일</span><i></i>2026-10-29 19:28:42</span><span>게시일자 : 2026-10-29 ~ 2026-11-17</span></p></div><div class="board-view-cont">본문</div>')
        self.assertEqual(value['publication_time'],'2026-10-29')

    def test_english_school_date_not_modification_or_event_date(self):
        value=self.meta('<dl><dt>date</dt><dd>2021.03.19</dd><dt>modification day</dt><dd>2026.11.03</dd></dl><div class="artclView"><dt>date</dt><dd>2026.11.10</dd></div>')
        self.assertEqual(value['publication_time'],'2021-03-19')

    def test_structured_article_date_not_event_start(self):
        value=self.meta('<script type="application/ld+json">{"@graph":[{"@type":"Event","startDate":"2026-11-10"},{"@type":"Article","datePublished":"2026-11-03T01:00:00+09:00"}]}</script>')
        self.assertEqual(value['publication_time'],'2026-11-03')

    def test_unknown_layout_and_no_date_remain_unknown(self):
        result=self.meta('<section>행사 2026-11-03</section>')
        self.assertEqual(result['layout'],'unknown')
        self.assertFalse(result['publication_time_is_explicit'])

    def test_images_are_listed_but_never_downloaded(self):
        value=self.meta('<div class="board-view-files"><a href="/file/download.do?id=1">포스터.jpg</a><a href="/file/download.do?id=2">양식.hwp</a></div>')
        self.assertTrue(value['attachment_scan_complete'])
        self.assertTrue(value['attachment_links'][0]['image_excluded_from_download'])
        self.assertFalse(value['attachment_links'][1]['image_excluded_from_download'])

    def test_full_body_retained_deduplicated_and_local_attachment_hashed(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'신청서.pdf';file.write_bytes(b'document')
            original='<div>대상: 학부생<p>'+('원문'*5000)+'</p><form><input value="secret"></form><script>secret</script><img src="poster.png"></div>'
            ann=SimpleNamespace(title='2026 겨울계절 교류수학 서울대학교',content_text='원문'*5000,content_html=original,metadata={},url='https://test.pusan.ac.kr/1',files=[str(file)])
            first=record_collection(temp,ann,'학부');second=record_collection(temp,ann,'학부')
            self.assertEqual(first['sha256'],second['sha256'])
            root=Path(temp)/'.runtime/intelligence/collection'
            payload=json.loads(gzip.decompress((root/first['artifact']).read_bytes()))
            self.assertEqual(payload['body_text'],ann.content_text)
            self.assertNotIn('secret',payload['body_html'])
            self.assertIn('poster.png',payload['body_html'])
            self.assertIn('original_publication_date',payload['missing_fields'])
            self.assertEqual(payload['attachments'][0]['sha256'],hashlib.sha256(b'document').hexdigest())
            db=sqlite3.connect(root/'index.sqlite3')
            try:self.assertEqual(db.execute('SELECT observations FROM samples').fetchone()[0],2)
            finally:db.close()
            self.assertEqual(ann.content_html,original)

    def test_evidence_failure_does_not_block_publication(self):
        import together.operations.events
        ann=SimpleNamespace(url='https://test.pusan.ac.kr/1')
        with patch('together.notices.intelligence.collection.record_collection',side_effect=OSError('disk')),patch('together.operations.events.event') as event:
            self.assertIsNone(observe_collection(ann,'학부','unused'))
            event.assert_called_once()


if __name__=='__main__':unittest.main()
