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
    def test_download_hashes_forty_megabytes_as_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=MagicMock();driver.get_cookies.return_value=[]
            editor=PlatoPostEditor(types.SimpleNamespace(driver=driver),temp)
            chunk=b'a'*(1024**2)
            with patch('together.notices.intelligence.post_editor.requests.Session') as factory:
                response=factory.return_value.get.return_value.__enter__.return_value
                response.status_code=200
                response.iter_content.return_value=(chunk for _ in range(40))
                result=editor._download({'name':'large.pdf','url':'https://dev-plato.pusan.ac.kr/file'})
            digest=hashlib.sha256()
            for _ in range(40):digest.update(chunk)
            self.assertEqual(result['bytes'],40*1024**2)
            self.assertEqual(result['sha256'],digest.hexdigest())

    def test_twelve_recent_verified_backups_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            editor=PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()),temp)
            for i in range(12):
                folder=Path(temp)/str(i);folder.mkdir();(folder/'verified.json').write_text('{}')
            editor._prune()
            self.assertEqual(len(list(Path(temp).iterdir())),12)

    def test_low_free_space_stops_before_any_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            editor=PlatoPostEditor(types.SimpleNamespace(driver=MagicMock(),safe_get=MagicMock()),temp)
            editor._attachment_links=MagicMock(return_value=[])
            editor._open_edit=MagicMock()
            with patch('together.notices.intelligence.post_editor.WebDriverWait'),patch('together.notices.intelligence.post_editor.shutil.disk_usage',return_value=types.SimpleNamespace(free=1024**3)):
                with self.assertRaisesRegex(RuntimeError,'Backup capacity reached'):
                    editor.read('https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2',backup=True)
            editor._open_edit.assert_not_called()

    def test_new_file_can_be_added_after_twelve_existing_attachments(self):
        from together.notices.intelligence.post_editor import snapshot_signature
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'new.txt';path.write_bytes(b'new')
            old=[{'name':f'{i}.txt','sha256':hashlib.sha256(str(i).encode()).hexdigest()} for i in range(12)]
            before={'subject':'제목','body_html':'<p>본문</p>','files':old,'notice':False,'backup_dir':temp}
            before['signature']=snapshot_signature(before)
            after=dict(before,files=old+[{'name':'new.txt','sha256':hashlib.sha256(b'new').hexdigest()}])
            after['signature']=snapshot_signature(after)
            editor=PlatoPostEditor(types.SimpleNamespace(driver=MagicMock()),temp)
            editor.read=MagicMock(return_value=before);editor._upload=MagicMock();editor._set_fields=MagicMock();editor._save=MagicMock(return_value=after)
            result=editor.update('https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2','제목','<p>본문</p>',[path])
            self.assertEqual(len(result['after']['files']),13)
            editor._upload.assert_called_once_with(path)

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
