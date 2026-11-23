import json,tempfile,unittest,types
from pathlib import Path
from unittest.mock import patch
from together.notices.intelligence.representative import verify_representative
from together.notices.intelligence.enrichment import PublishedNoticeStore

TITLE='제7회 국립대학육성사업 영상 콘텐츠 공모전 개최 안내'
SOURCE='https://bce.pusan.ac.kr/bbs/bce/220/1471295/artclView.do'
BOARD='https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=12'
ARTICLE='https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=12&bwid=34'
class Response:
 def __init__(self,text):self.text=text;self.status_code=200
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def raise_for_status(self):pass
class RepresentativeRetentionTests(unittest.TestCase):
 def test_actual_old_representative_is_checked_before_unrelated_recent_candidates(self):
  calls=[]
  def get(url,**kwargs):
   calls.append(url)
   if 'view.php' in url:return Response('<form id="ubboard_list_form"><a href="article.php?id=12&bwid=34">'+TITLE+'</a></form>')
   return Response('<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div><div class="content"><p>본문 링크 : <a href="'+SOURCE+'">원문</a></p><p>접수2026.11.2~12.27, 제7회 영상공모전</p></div></div>')
  record={'article_url':ARTICLE+'9','sources':['https://other.pusan.ac.kr/job'],'notice':{'title':'직원채용 합격자 공고','source_url':'https://other.pusan.ac.kr/job','body_text':'다른 행사'}}
  client=types.SimpleNamespace(get=get,board_url=lambda _:BOARD)
  with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
   store.return_value.load.return_value=[record]
   result=verify_representative(types.SimpleNamespace(_http=client),{'title':TITLE,'source_url':SOURCE},root,['공모전'],current_notice={'title':'[학사]'+TITLE})
  self.assertEqual(result['status'],'verified');self.assertEqual(result['article_url'],ARTICLE);self.assertNotIn('_actual_candidates',result);self.assertEqual(result['post_or_send_requests'],0)
 def test_unbound_alias_returns_actual_body_for_recheck_not_a_title_only_suppression(self):
  def get(url,**kw):
   if 'view.php' in url:return Response('<form id="ubboard_list_form"><a href="article.php?id=12&bwid=34">'+TITLE+'</a></form>')
   return Response('<div class="ubboard_view"><div class="subject"><h3>'+TITLE+'</h3></div><div class="content"><p>본문 링크 : <a href="'+SOURCE+'">원문</a></p><p>실제 최초 원문의 작품 분량과 시상 조건</p></div></div>')
  record={'article_url':ARTICLE+'9','sources':[],'notice':{'title':'무관한 공지','source_url':'https://other.pusan.ac.kr/view','body_text':'무관한 본문'}}
  with tempfile.TemporaryDirectory() as root,patch('together.notices.intelligence.representative.PublishedNoticeStore') as store:
   store.return_value.load.return_value=[record]
   result=verify_representative(types.SimpleNamespace(_http=types.SimpleNamespace(get=get,board_url=lambda _:BOARD)),{'title':TITLE,'source_url':'https://new.pusan.ac.kr/alias'},root,['공모전'],current_notice={'title':TITLE})
  self.assertEqual(result['status'],'recheck');self.assertEqual(result['_actual_candidates'][0]['source_url'],SOURCE);self.assertIn('작품 분량',result['_actual_candidates'][0]['body_excerpt'])
 def test_age_does_not_erase_still_published_representative(self):
  with tempfile.TemporaryDirectory() as root:
   store=PublishedNoticeStore(root);row={'article_url':ARTICLE,'notice':{'title':TITLE,'observed_at':'2025-01-01T12:00:00'},'sources':[SOURCE]};store.put(row)
   store.put({'article_url':ARTICLE+'9','notice':{'title':'새 글','observed_at':'2026-11-23T12:00:00'},'sources':[]})
   self.assertEqual(len(store.load()),2);self.assertEqual(store.load()[0]['article_url'],ARTICLE)
