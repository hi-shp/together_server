import unittest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup
from together.crawler.counts import notice_count
from together.crawler.article_id_boards import last_page_size


class BoardCountTests(unittest.TestCase):
    def label(self, html, count=10):
        return notice_count(BeautifulSoup(html, "html.parser"), count)

    def test_official_total_wins_over_estimate(self):
        self.assertEqual(self.label('<div class="board-total"><span class="total">전체 : 118</span></div>'), 118)

    def test_last_page_estimate_uses_page_count(self):
        self.assertEqual(self.label('<div class="_paging"><a class="_last" href="javascript:page_link(\'3\')">끝</a></div>'), 30)

    def test_body_numbers_and_navigation_group_do_not_become_total(self):
        self.assertEqual(self.label('<div>전체: 1511856</div><div class="_paging"><a>10</a></div>'), 10)

    def test_single_and_empty_page(self):
        self.assertEqual(self.label('<div class="_paging"><span class="_totPage">1</span></div>', 0), 0)

    def test_partial_last_page_gives_count(self):
        soup = BeautifulSoup('<div class="_paging"><span class="_totPage">3</span></div>', "html.parser")
        self.assertEqual(notice_count(soup, 10, lambda page: 9), 29)
        self.assertEqual(notice_count(soup, 10, lambda page: None), 30)

    def test_last_page_read_checks_page_and_board_identity(self):
        first = BeautifulSoup('<form name="pageForm" action="/bbs/test/2/artclList.do"><input name="page" value="1"></form>', "html.parser")
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = "https://test.pusan.ac.kr/bbs/test/2/artclList.do?page=3"
        response.text = '<form name="pageForm" action="/bbs/test/2/artclList.do"><input name="page" value="3"></form><a href="/bbs/test/2/999/artclView.do">공지</a>'
        with patch("together.crawler.http.get", return_value=response) as get:
            self.assertEqual(last_page_size(first, "https://test.pusan.ac.kr/list", "k2web:test:2", 3), 1)
            self.assertEqual(get.call_args.kwargs["params"]["page"], 3)
            response.text = response.text.replace('value="3"', 'value="1"')
            self.assertIsNone(last_page_size(first, "https://test.pusan.ac.kr/list", "k2web:test:2", 3))

    def test_failed_count_lookup_does_not_fail_collection(self):
        import requests
        first = BeautifulSoup('<form name="pageForm" action="/bbs/test/2/artclList.do"></form>', "html.parser")
        with patch("together.crawler.http.get", side_effect=requests.Timeout):
            self.assertIsNone(last_page_size(first, "https://test.pusan.ac.kr/list", "k2web:test:2", 3))
