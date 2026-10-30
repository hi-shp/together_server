import time
import types
import unittest
from unittest.mock import patch

from together.crawler.prefetch import BoardSnapshot, OrderedBoardPrefetch


class OrderedBoardPrefetchTests(unittest.TestCase):
    def test_default_window_keeps_more_ordered_requests_ready(self):
        prefetch = OrderedBoardPrefetch([])
        try:
            self.assertEqual(prefetch.window, 12)
            self.assertEqual(prefetch.executor._max_workers, 8)
        finally:
            prefetch.close()

    def test_small_window_returns_each_snapshot_at_its_original_index(self):
        pages = [
            types.SimpleNamespace(page_url=f"https://board{i}.pusan.ac.kr/list")
            for i in range(5)
        ]

        def fetch(url):
            index = int(url.split("board", 1)[1].split(".", 1)[0])
            time.sleep((4 - index) * 0.002)
            return BoardSnapshot(url, f"page-{index}")

        with patch("together.crawler.prefetch._fetch", side_effect=fetch):
            prefetch = OrderedBoardPrefetch(pages, workers=3, window=3)
            try:
                values = [prefetch.take(index).result().text for index in range(5)]
            finally:
                prefetch.close()
        self.assertEqual(values, [f"page-{index}" for index in range(5)])

    def test_university_and_authenticated_boards_are_not_prefetched(self):
        pages = [
            types.SimpleNamespace(
                page_url="https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?page=1"
            ),
            types.SimpleNamespace(
                page_url="https://onestop.pusan.ac.kr/page?menuCD=1"
            ),
        ]
        with patch("together.crawler.prefetch._fetch") as fetch:
            prefetch = OrderedBoardPrefetch(pages)
            try:
                self.assertIsNone(prefetch.take(0))
                self.assertIsNone(prefetch.take(1))
            finally:
                prefetch.close()
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
