import json
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import MagicMock,patch
from together.notices.intelligence import recovery
from together.notices.intelligence.post_editor import PostEditError
from together.plato.star_cleanup import collect_old_stars

class StarDateAndMoveTests(unittest.TestCase):
    def response(self,text):
        r=MagicMock();r.text=text;r.__enter__.return_value=r;return r
    def test_title_date_cannot_remove_todays_star(self):
        h=MagicMock();h.get.return_value=self.response('<table><tbody><tr><td><a href="article.php?id=1&bwid=2">⭐2020-01-01 행사</a></td><td>2026-11-20</td></tr></tbody></table>')
        self.assertEqual(collect_old_stars(h,'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=1','2026-11-20')[0],[])
    def test_old_post_uses_actual_date_even_when_title_says_future(self):
        h=MagicMock();h.get.return_value=self.response('<table><tbody><tr><td><a href="article.php?id=1&bwid=2">⭐2030-01-01 행사</a></td><td>2026-11-19</td></tr></tbody></table>')
        self.assertEqual(len(collect_old_stars(h,'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=1','2026-11-20')[0]),1)
    def test_title_date_does_not_substitute_missing_metadata(self):
        h=MagicMock();h.get.return_value=self.response('<table><tbody><tr><td><a href="article.php?id=1&bwid=2">⭐2020-01-01 행사</a></td></tr></tbody></table>')
        with self.assertRaises(PostEditError):collect_old_stars(h,'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=1','2026-11-20')
    def test_move_formats_today_old_and_excluded_boards(self):
        h=MagicMock();today=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        h.get.return_value=self.response('<div class="ubboard_view"><div class="well"><div class="info">작성일 '+today+'</div></div></div>')
        self.assertEqual(recovery._relocated_subject(h,'url','[학과] 행사','교육/특강/프로그램'),'⭐[학과] 행사')
        self.assertEqual(recovery._relocated_subject(h,'url','⭐행사','장학금'),'행사')
        h.get.return_value=self.response('<div class="ubboard_view"><div class="well"><div class="info">작성일 2020-01-01</div></div></div>')
        self.assertEqual(recovery._relocated_subject(h,'url','⭐행사','교육/특강/프로그램'),'행사')
        h.get.return_value=self.response('<div class="content">2026-11-20</div>')
        with self.assertRaises(PostEditError):recovery._relocated_subject(h,'url','행사','교육/특강/프로그램')
    def test_move_star_comparison_does_not_hide_content_or_attachment_change(self):
        a={'subject':'행사','body_html':'<p>본문</p>','notice':False,'files':[{'name':'안내.pdf','sha256':'same'}]}
        self.assertTrue(recovery._same_relocated_material(a,dict(a,subject='⭐행사')))
        for changes in ({'body_html':'<p>다른 내용</p>'},{'files':[]},{'notice':True},{'subject':'다른 행사'}):self.assertFalse(recovery._same_relocated_material(a,dict(a,**changes)))
    def test_uncertain_title_save_is_reconciled_before_delete_without_resubmit(self):
        source='https://school.invalid/post';old='https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2';new='https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=3&bwid=4'
        original={'subject':'행사','body_html':'<p>본문</p>','notice':False,'files':[],'signature':'old'}
        receipt={'state':'verified','board':'기타 공지사항','article_url':old,'source_url':source}
        for saved in (False,True):
            with self.subTest(saved=saved),tempfile.TemporaryDirectory() as temp:
                p=Path(temp)/'.runtime/intelligence/recovery/x.json';p.parent.mkdir(parents=True)
                p.write_text(json.dumps({'phase':'title_format_submitting','title_format_submitting':True,'original':original,'original_receipt':receipt,'original_record':{'article_url':old,'notice':{'title':'행사'},'signature':'old'}}))
                copied=dict(original,subject='⭐행사' if saved else '행사',signature='new')
                service=MagicMock();service._http.board_url.side_effect=lambda name:name
                with patch.object(recovery,'known_receipt',return_value=receipt),patch.object(recovery,'_links',side_effect=lambda client,board,subject:{'4':new} if board=='교육/특강/프로그램' else {}),patch.object(recovery,'_relocated_subject',return_value='⭐행사'),patch.object(recovery,'PublishedNoticeStore'),patch.object(recovery,'remember_receipt') as remember,patch.object(recovery,'_delete_once') as delete,patch.object(recovery,'PlatoPostEditor') as editor:
                    editor.return_value.read.return_value=copied
                    result=recovery.move_category(service,temp,source,'교육/특강/프로그램','x')
                    editor.return_value.update.assert_not_called();service._http.post_once.assert_not_called();delete.assert_not_called()
                    self.assertEqual(result['done'],saved)
                    self.assertEqual(remember.call_count,int(saved))
                    if saved:self.assertEqual(remember.call_args.args[1]['subject'],'⭐행사')
