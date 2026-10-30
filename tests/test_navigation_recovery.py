import unittest
from unittest.mock import MagicMock, patch
from together.plato.service import WriteNoticeService


class NavigationRecovery(unittest.TestCase):
    def service(self, urls):
        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = MagicMock()
        states = iter(urls)
        service.driver.get.side_effect = lambda url: setattr(service.driver, "current_url", next(states))
        service.restart_driver = MagicMock()
        service.login_ready = MagicMock()
        service.login = MagicMock()
        service.plato_id, service.plato_pw = "fixture", "fixture"
        return service

    @patch("together.plato.service.time.sleep")
    @patch("together.plato.service.event")
    def test_error_page_is_retried_before_login_fields_are_used(self, event, sleep):
        target = "https://dev-plato.pusan.ac.kr/"
        service = self.service(["chrome-error://chromewebdata/", target])
        service.safe_get(target)
        self.assertEqual(service.driver.get.call_count, 2)
        service.restart_driver.assert_called_once_with()
        service.driver.find_elements.assert_not_called()
        service.login.assert_not_called()
        self.assertEqual(service.driver.current_url, target)

    @patch("together.plato.service.time.sleep")
    @patch("together.plato.service.event")
    def test_connection_error_after_login_also_reconnects(self, event, sleep):
        target = "https://dev-plato.pusan.ac.kr/course/view.php?id=157301"
        service = self.service([target, "chrome-error://chromewebdata/", target])
        service.driver.find_elements.side_effect = [[object()], []]
        service.safe_get(target, login_required=True)
        self.assertEqual(service.driver.get.call_count, 3)
        service.restart_driver.assert_called_once_with()
        service.login_ready.assert_called_once_with()
        self.assertEqual(service.login.call_count, 2)
        self.assertEqual(service.driver.current_url, target)

    @patch("together.plato.service.time.sleep")
    @patch("together.plato.service.event")
    def test_document_error_page_is_detected_even_when_address_bar_keeps_requested_url(self, event, sleep):
        target = "https://dev-plato.pusan.ac.kr/mod/feedback/view.php?id=2007210"
        service = self.service([target, target])
        service.driver.execute_script.side_effect = ['chrome-error://chromewebdata/', target]
        service.safe_get(target)
        self.assertEqual(service.driver.get.call_count, 2)
        service.restart_driver.assert_called_once_with()
