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

    def test_linked_department_alias_uses_verified_primary_body_source(self):
        primary=SOURCE.replace('123','111')
        body='<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div><a href="'+primary+'">본문</a></div>'
        client=types.SimpleNamespace(get=lambda *a,**k:Response(body),board_url=lambda _:BOARD)
        record={'article_url':ARTICLE,'sources':[primary,SOURCE],'notice':{'source_url':primary,'title':TITLE}}
        self.assertEqual(self.run_check(client,[record])['status'],'verified')
        # Same title without a binding cannot borrow the primary's link.
        self.assertEqual(self.run_check(client,[dict(record,sources=[primary])])['status'],'pending')
        record['notice']['source_url']=SOURCE.replace('123','222')
        self.assertEqual(self.run_check(client,[record])['status'],'pending')
        self.assertEqual(self.run_check(client,[record,dict(record,article_url=ARTICLE+'9')])['status'],'pending')

    def test_first_post_can_proceed_when_lookup_fails_but_never_reposts_an_uncertain_save(self):
        from together.notices.intelligence.publication import remember_receipt
        data={'title':TITLE,'current_source_url':SOURCE}
        with tempfile.TemporaryDirectory() as tmp:
            args=(types.SimpleNamespace(),data,tmp,['기타 공지사항'])
            result=verify_representative(*args,current_notice={'title':TITLE})
            self.assertEqual(result['status'],'first_publication')
            remember_receipt(tmp,{'source_url':SOURCE,'state':'submitting','subject':TITLE})
            self.assertEqual(verify_representative(*args,current_notice={'title':TITLE})['status'],'pending')

    def test_every_school_category_search_must_complete_before_missing(self):
        calls=[]
        def get(url,**kw):calls.append(url);return Response('<form id="ubboard_list_form"><table><tbody></tbody></table></form>')
        result=self.run_check(types.SimpleNamespace(get=get,board_url=lambda _:BOARD))
        self.assertEqual(result['status'],'missing')
        self.assertEqual(result['searched_boards'],1)

    def test_unclassified_label_searches_the_actual_miscellaneous_board(self):
        looked_up = []
        def board_url(name):
            looked_up.append(name)
            if name == '해당없음':
                raise ValueError('Classification label is not a PLATO board')
            return BOARD
        client = types.SimpleNamespace(board_url=board_url,
            get=lambda *a, **k: Response('<form id="ubboard_list_form"></form>'))
        with tempfile.TemporaryDirectory() as root, patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
            store.return_value.load.return_value = []
            result = verify_representative(types.SimpleNamespace(_http=client),
                {'title': TITLE}, root, ['교육/특강/프로그램', '해당없음', '기타 공지사항'])
        self.assertEqual(looked_up, ['교육/특강/프로그램', '기타 공지사항'])
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['searched_boards'], 2)

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

    def test_same_title_unbound_department_is_rechecked_using_verified_primary(self):
        primary=SOURCE.replace('123','111')
        html='<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div><a href="'+primary+'">본문</a></div>'
        record={'article_url':ARTICLE,'sources':[primary],'notice':{'title':TITLE,'source_url':primary,'body_text':'대상 학교와 학기'}}
        client=types.SimpleNamespace(get=lambda *a,**k:Response(html),board_url=lambda _:BOARD)
        with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
            store.return_value.load.return_value=[record]
            result=verify_representative(types.SimpleNamespace(_http=client),
                {'title':TITLE,'source_url':SOURCE,'current_source_url':SOURCE},root,['기타 공지사항'],current_notice={'title':TITLE})
            self.assertEqual(result['status'],'recheck')
            self.assertEqual(result['_actual_candidates'][0]['source_url'],primary)
            # Only the follow-up decision against the actual primary can suppress.
            followup=verify_representative(types.SimpleNamespace(_http=client),
                {'title':TITLE,'source_url':primary,'current_source_url':SOURCE},root,['기타 공지사항'],current_notice={'title':TITLE})
            self.assertEqual(followup['status'],'verified')
            self.assertEqual(result['post_or_send_requests'],0)

    def test_unbound_department_cannot_borrow_a_manually_changed_primary_link(self):
        primary=SOURCE.replace('123','111')
        record={'article_url':ARTICLE,'sources':[primary],'notice':{'title':TITLE,'source_url':primary}}
        client=types.SimpleNamespace(get=lambda *a,**k:Response('<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div></div>'),board_url=lambda _:BOARD)
        with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
            store.return_value.load.return_value=[record]
            result=verify_representative(types.SimpleNamespace(_http=client),
                {'title':TITLE,'source_url':SOURCE,'current_source_url':SOURCE},root,['기타 공지사항'],current_notice={'title':TITLE})
            self.assertEqual(result['status'],'pending')

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
        with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store, patch('together.notices.intelligence.representative._archived_title',return_value=None):
            store.return_value.load.return_value=[record]
            client=MagicMock()
            result=verify_representative(types.SimpleNamespace(_http=client),{'title':TITLE},root,['기타 공지사항'],current_notice={'title':TITLE})
            self.assertEqual(result['status'],'recheck')
            self.assertEqual(result['_actual_candidates'][0]['source_url'],SOURCE)
            client.get.assert_not_called()

    def test_school_lowercased_literal_search_keeps_the_same_query(self):
        from together.notices.intelligence.representative import _search
        client = types.SimpleNamespace(get=lambda *a, **k: Response(
            '<form id="ubboard_list_form"></form><input name="keyword" value="google">'))
        self.assertEqual(_search(client, BOARD, 'Google Chrome 보안 업데이트'), ([], 1))

    def test_school_search_for_a_different_word_is_still_rejected(self):
        from together.notices.intelligence.representative import _search, RepresentativeUnconfirmed
        client = types.SimpleNamespace(get=lambda *a, **k: Response(
            '<form id="ubboard_list_form"></form><input name="keyword" value="unrelated">'))
        with self.assertRaises(RepresentativeUnconfirmed):
            _search(client, BOARD, 'Google Chrome 보안 업데이트')

    def test_unbound_korean_alias_rechecks_actual_korean_post_not_three_english_posts(self):
        from unittest.mock import MagicMock
        english = '2026학년도 겨울학기 대학원 학위청구 외국어시험(영어) 대체강좌 수강생 모집'
        korean = english.replace('영어', '한국어')
        previous = '2026학년도 겨울학기 대학원 학위청구 외국어시험 대체강좌 (한국어) 수강생 모집'
        titles = [english, english, '[대학원] ' + english + ' 안내', previous]
        records = [{'article_url': ARTICLE + str(i), 'sources': [SOURCE + str(i)],
                    'notice': {'title': title, 'source_url': SOURCE + str(i), 'body_text': '공식 강좌 본문'}}
                   for i, title in enumerate(titles)]
        with tempfile.TemporaryDirectory() as root, patch('together.notices.intelligence.representative.PublishedNoticeStore') as store, patch('together.notices.intelligence.representative._archived_title', return_value=None):
            store.return_value.load.return_value = records
            result = verify_representative(types.SimpleNamespace(_http=MagicMock()),
                {'title': '학교 검색에 없는 이력 별칭'}, root, ['교육/특강/프로그램'], current_notice={'title': korean})
            self.assertEqual(result['status'], 'recheck')
            self.assertEqual([v['title'] for v in result['_actual_candidates']], [previous])

    def test_published_title_variant_is_verified_before_fragile_alias_search(self):
        from unittest.mock import MagicMock
        title = '연구윤리 표절예방 프로그램 Turnitin 이용교육 안내'
        alias = '[도서관]연구윤리 표절예방 프로그램 Turnitin 이용교육 안내'
        current = '대원, 교대원] 도서관 연구윤리 표절예방 프로그램 Turnitin 이용교육 안내'
        record = {'article_url': ARTICLE, 'sources': [SOURCE],
                  'notice': {'title': title, 'source_url': SOURCE, 'body_text': ''}}
        client = MagicMock()
        with tempfile.TemporaryDirectory() as root, patch('together.notices.intelligence.representative.PublishedNoticeStore') as store, patch('together.notices.intelligence.representative._article', return_value=True) as article, patch('together.notices.intelligence.representative._archived_title', side_effect=TimeoutError('alias search unavailable')) as search:
            store.return_value.load.return_value = [record]
            result = verify_representative(types.SimpleNamespace(_http=client),
                {'title': alias, 'current_source_url': SOURCE + '9'}, root,
                ['교육/특강/프로그램'], current_notice={'title': current})
            self.assertEqual(result['status'], 'recheck')
            self.assertEqual(result['_actual_candidates'][0]['source_url'], SOURCE)
            article.assert_called_once_with(client, ARTICLE, title, SOURCE, indexed=True)
            search.assert_not_called()

    def test_failed_known_title_variant_read_cannot_become_first_publication(self):
        title = '연구윤리 표절예방 프로그램 Turnitin 이용교육 안내'
        record = {'article_url': ARTICLE, 'sources': [SOURCE],
                  'notice': {'title': title, 'source_url': SOURCE}}
        for error in [TimeoutError('temporary read failure'), ValueError('changed source link')]:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as root, patch('together.notices.intelligence.representative.PublishedNoticeStore') as store, patch('together.notices.intelligence.representative._article', side_effect=error), patch('together.notices.intelligence.representative._archived_title', side_effect=TimeoutError('search unavailable')):
                store.return_value.load.return_value = [record]
                result = verify_representative(types.SimpleNamespace(_http=types.SimpleNamespace()),
                    {'title': '[도서관]' + title, 'current_source_url': SOURCE + '9'}, root,
                    ['교육/특강/프로그램'], current_notice={'title': '대원, 교대원] 도서관 ' + title})
                self.assertEqual(result['status'], 'pending')
                self.assertEqual(result['post_or_send_requests'], 0)

    def test_unrelated_published_post_does_not_block_a_first_notice(self):
        record = {'article_url': ARTICLE, 'sources': [SOURCE],
                  'notice': {'title': '2026 지역기업 신입사원 채용', 'source_url': SOURCE}}
        with tempfile.TemporaryDirectory() as root, patch('together.notices.intelligence.representative.PublishedNoticeStore') as store, patch('together.notices.intelligence.representative._archived_title', side_effect=TimeoutError('search unavailable')):
            store.return_value.load.return_value = [record]
            result = verify_representative(types.SimpleNamespace(_http=types.SimpleNamespace()),
                {'title': '연구윤리 표절예방 프로그램 Turnitin 교육', 'current_source_url': SOURCE + '9'},
                root, ['교육/특강/프로그램'], current_notice={'title': '도서관 Turnitin 이용교육'})
            self.assertEqual(result['status'], 'first_publication')

if __name__=='__main__':unittest.main()
