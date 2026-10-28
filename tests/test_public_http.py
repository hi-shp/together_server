"""같은 학교 서버의 연결 재사용과 조회 간격을 확인한다."""

from unittest.mock import patch, MagicMock
import unittest
from together.crawler import http


class PublicHttpTests(unittest.TestCase):
    def test_connections_are_reused_without_a_new_fixed_delay(self):
        session = MagicMock()
        with (
            patch.object(http, "_session", session),
            patch.object(http, "origin_group", return_value="164.125.6.250"),
            patch.object(http, "_next_request", {}),
            patch.object(http.time, "monotonic", return_value=100),
            patch.object(http.time, "sleep") as sleep,
        ):
            http.get("https://eec.pusan.ac.kr/first", timeout=(5, 30))
            http.get("https://semi.pusan.ac.kr/second", timeout=(5, 30))
            sleep.assert_not_called()
            self.assertEqual(session.get.call_count, 2)
            self.assertEqual(session.get.call_args.kwargs["timeout"], (5, 30))

    def test_other_servers_do_not_wait_for_the_first_server(self):
        with (
            patch.object(http, "_session", MagicMock()),
            patch.object(http, "origin_group", side_effect=["one", "two"]),
            patch.object(http, "_next_request", {}),
            patch.object(http.time, "monotonic", return_value=100),
            patch.object(http.time, "sleep") as sleep,
        ):
            http.get("https://eec.pusan.ac.kr/first")
            http.get("https://mse.pusan.ac.kr/second")
            sleep.assert_not_called()
