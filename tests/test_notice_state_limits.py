import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from together.notices.intelligence.publication import known_receipt, remember_receipt
from together.notices.intelligence.enrichment import PublishedNoticeStore
from together.notices.intelligence.events import Notice
from together.notices.intelligence.post_editor import PlatoPostEditor


class NoticeStateLimits(unittest.TestCase):
    def test_receipts_remain_readable_after_four_megabytes(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt={'source_url':'https://example.invalid/notice','subject':'공지','validation':{'body':'a'*(4*1024**2)}}
            remember_receipt(temp,receipt)
            self.assertEqual(known_receipt(temp,receipt['source_url'])['subject'],'공지')
            remember_receipt(temp,dict(receipt,state='verified'))
            self.assertEqual(known_receipt(temp,receipt['source_url'])['state'],'verified')

    def test_korean_publication_index_keeps_working_beyond_sixteen_megabytes(self):
        with tempfile.TemporaryDirectory() as temp:
            store=PublishedNoticeStore(temp)
            stamp=datetime.now().isoformat()
            records=[{'article_url':f'https://example.invalid/{i}','notice':{'observed_at':stamp,'body_text':'가'*6000}} for i in range(1000)]
            store.path.write_text(json.dumps({'schema':1,'posts':records},ensure_ascii=False),encoding='utf-8')
            self.assertGreater(store.path.stat().st_size,16*1024**2)
            self.assertEqual(len(store.load()),1000)
            store.put({'article_url':'https://example.invalid/new','notice':{'observed_at':stamp}})
            self.assertEqual(store.load()[-1]['article_url'],'https://example.invalid/new')

    def test_attachment_inventory_checks_all_twelve_existing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=MagicMock();driver.find_element.return_value.get_attribute.return_value='기존 내용'
            driver.find_elements.return_value=[]
            editor=PlatoPostEditor(types.SimpleNamespace(driver=driver,safe_get=MagicMock()),temp)
            links=[{'name':f'{i}.txt','url':f'https://dev-plato.pusan.ac.kr/file/{i}'} for i in range(12)]
            editor._attachment_links=MagicMock(return_value=links)
            editor._file_names=MagicMock(return_value=[x['name'] for x in links])
            editor._open_edit=MagicMock()
            editor._download=MagicMock(side_effect=lambda item,*args:dict(item,bytes=1,sha256=hashlib.sha256(item['name'].encode()).hexdigest()))
            with patch('together.notices.intelligence.post_editor.WebDriverWait'):
                result=editor.read('https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2')
            self.assertEqual(len(result['files']),12)
            self.assertEqual(editor._download.call_count,12)

    def test_further_material_improvement_is_not_stopped_after_three_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Notice('2026년 제3기 해양정보 로봇 개발 캠프 참가자 모집',datetime.now().isoformat(),source_url='https://example.invalid/old',body_text='모집 안내')
            record={'article_url':'https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2','notice':asdict(old),'sources':[old.source_url],'updates':5}
            store=PublishedNoticeStore(temp);store.put(record)
            ann=types.SimpleNamespace(title=old.title,url='https://example.invalid/new',content_text='신청방법: 온라인',metadata={'attachment_links':[{'url':'https://example.invalid/file'}]})
            self.assertIsNotNone(store.find(ann,'기계'))
