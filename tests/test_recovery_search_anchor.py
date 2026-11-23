import unittest
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from together.notices.intelligence.recovery import _links


class RecoverySearchAnchorTests(unittest.TestCase):
    def test_punctuation_normalizing_school_search_still_finds_an_existing_copy(self):
        client = MagicMock()
        response = client.get.return_value.__enter__.return_value
        response.text = '''<form id="ubboard_list_form">
          <input name="manage[]" value="2475001">
          <a href="article.php?id=1885169&amp;bwid=2475001">실제 복사본</a></form>'''
        board = 'https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=1885169'
        found = _links(client, board, '⭐[대학공지] (2026) [11.30.(월) 마감] 융합전공 운영 사례 원고 모집 안내')
        query = parse_qs(urlsplit(client.get.call_args.args[0]).query)
        self.assertEqual(query['keyword'], ['융합전공'])
        self.assertEqual(set(found), {'2475001'})
        client.post_once.assert_not_called()
