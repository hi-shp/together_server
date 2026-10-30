"""목록 처리 순서를 유지하는 작은 공개 게시판 사전조회 창."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from together.crawler.http import get as http_get
from together.config import HTTP_TIMEOUT


@dataclass(frozen=True)
class BoardSnapshot:
    url: str
    text: str

    def raise_for_status(self):
        return None


def _eligible(url):
    return (
        url.startswith(("http://", "https://"))
        and "onestop.pusan.ac.kr/" not in url
        and "CMS/Board/PopupBoard.do" not in url
        and ".invalid/" not in url
    )


def _fetch(url):
    with http_get(url, verify=False, timeout=HTTP_TIMEOUT) as response:
        response.raise_for_status()
        return BoardSnapshot(response.url, response.text)


class OrderedBoardPrefetch:
    """최대 window개만 미리 읽고 결과는 원래 인덱스에서만 꺼낸다."""

    def __init__(self, pages, *, workers=3, window=3):
        self.pages = pages
        self.window = max(1, window)
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="board-prefetch"
        )
        self.futures = {}
        self.next_index = 0
        self._fill(0)

    def _fill(self, current_index):
        limit = min(len(self.pages), current_index + self.window)
        while self.next_index < limit:
            index = self.next_index
            self.next_index += 1
            url = self.pages[index].page_url
            if _eligible(url):
                self.futures[index] = self.executor.submit(_fetch, url)

    def take(self, index):
        future = self.futures.pop(index, None)
        self._fill(index + 1)
        return future

    def close(self):
        for future in self.futures.values():
            future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

