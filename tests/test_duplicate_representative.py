import json, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
from together.notices.intelligence.representative import verify_representative

BOARD='https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=12'
ARTICLE='https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=12&bwid=34'
SOURCE='https://ee.pusan.ac.kr/bbs/ee/10/123/artclView.do'
TITLE='2026 동계 서울시립대학교 교류수학 안내'

class Response:
    def __init__(self,text,status=200):self.text,self.status_code=text,status
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):
        if self.status_code>=400:raise OSError('school request failed')

class DuplicateRepresentativeTests(unittest.TestCase):
    def run_check(self,client,records=None):
        with tempfile.TemporaryDirectory() as tmp,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
            store.return_value.load.return_value=records or []
            result=verify_representative(types.SimpleNamespace(_http=client),{'title':TITLE,'source_url':SOURCE},tmp,['기타 공지사항'])
            files=list((Path(tmp)/'.runtime/intelligence/duplicate-representatives').glob('*.json'))
            self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['status'],result['status'])
            return result

    def test_indexed_title_alone_does_not_prove_a_post_exists(self):
        client=types.SimpleNamespace(get=lambda *a,**k:Response('<html>로그인 오류</html>'),board_url=lambda _:BOARD)
        record={'article_url':ARTICLE,'sources':[SOURCE],'notice':{'title':TITLE,'source_url':SOURCE}}
        self.assertEqual(self.run_check(client,[record])['status'],'pending')

    def test_verified_actual_source_survives_manual_title_change(self):
        body='<div class="ubboard_view"><div class="subject"><h3>직접 수정한 제목</h3></div><a href="'+SOURCE+'">본문 링크</a></div>'
        client=types.SimpleNamespace(get=lambda *a,**k:Response(body),board_url=lambda _:BOARD)
        record={'article_url':ARTICLE,'sources':[SOURCE],'notice':{'title':TITLE,'source_url':SOURCE}}
        result=self.run_check(client,[record])
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['searched_boards'],0)
        self.assertEqual(result['post_or_send_requests'],0)

    def test_every_school_category_search_must_complete_before_missing(self):
        calls=[]
        def get(url,**kw):calls.append(url);return Response('<form id="ubboard_list_form"><table><tbody></tbody></table></form>')
        result=self.run_check(types.SimpleNamespace(get=get,board_url=lambda _:BOARD))
        self.assertEqual(result['status'],'missing')
        self.assertEqual(result['searched_boards'],1)

    def test_representative_on_next_search_page_is_not_missed(self):
        calls=[]
        def get(url,**kw):
            calls.append(url)
            if 'article.php' in url:
                return Response('<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div><a href="'+SOURCE+'">본문</a></div>')
            if 'page=2' in url:
                return Response('<form id="ubboard_list_form"><a href="'+ARTICLE+'">'+TITLE+'</a></form>')
            return Response('<form id="ubboard_list_form"></form><div class="paging"><a href="'+BOARD+'&amp;page=2">2</a></div>')
        result=self.run_check(types.SimpleNamespace(get=get,board_url=lambda _:BOARD))
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['search_pages'],2)
        self.assertEqual(result['article_url'],ARTICLE)

    def test_request_failure_is_not_permission_to_repost(self):
        def get(*a,**kw):raise TimeoutError('school temporarily unavailable')
        result=self.run_check(types.SimpleNamespace(get=get,board_url=lambda _:BOARD))
        self.assertEqual(result['status'],'pending')
        self.assertEqual(result['post_or_send_requests'],0)

    def test_search_uses_a_literal_word_not_school_stripped_parentheses(self):
        from urllib.parse import urlsplit,parse_qs
        calls=[]
        def get(url,**kw):
            calls.append(url)
            keyword=parse_qs(urlsplit(url).query)['keyword'][0]
            self.assertNotIn('(',keyword)
            self.assertNotIn(')',keyword)
            return Response('<form id="ubboard_list_form"></form>')
        self.run_check(types.SimpleNamespace(get=get,board_url=lambda _:BOARD))
        self.assertTrue(calls)

    def test_unpublished_history_alias_is_rechecked_against_actual_posts(self):
        from together.notices.intelligence.representative import verify_representative
        from unittest.mock import MagicMock
        record={'article_url':ARTICLE,'sources':[SOURCE],'notice':{'title':'[학사과] 서울시립대 2026 겨울 학점교류 모집','source_url':SOURCE,'body_text':'원문 내용','department':'대학 공지'}}
        with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
            store.return_value.load.return_value=[record]
            client=MagicMock()
            result=verify_representative(types.SimpleNamespace(_http=client),{'title':TITLE},root,['기타 공지사항'],current_notice={'title':TITLE})
            self.assertEqual(result['status'],'recheck')
            self.assertEqual(result['_actual_candidates'][0]['source_url'],SOURCE)
            client.get.assert_not_called()

if __name__=='__main__':unittest.main()
