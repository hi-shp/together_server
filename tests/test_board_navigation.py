import unittest
import sys
import types
from unittest.mock import MagicMock, patch

sys.modules.setdefault(
    "together.integrations.openai_client",
    types.SimpleNamespace(answer_gpt=MagicMock(), check_title_similarity=MagicMock()),
)

from selenium.common.exceptions import TimeoutException
from together.plato.service import WriteNoticeService
from together.notices.intelligence.post_editor import PlatoPostEditor


class BoardNavigation(unittest.TestCase):
    def test_post_attachment_inventory_includes_image_sources_and_stays_on_target(self):
        service = self.service()
        image = "https://dev-plato.pusan.ac.kr/pluginfile.php/1/mod_ubboard/attachment/2/notice.jpg"
        service.driver.execute_script.return_value = [image, image + '?forcedownload=1', image, image.replace('/attachment/2/', '/attachment/3/'), image.replace('dev-plato.pusan.ac.kr', 'other.invalid')]
        editor = PlatoPostEditor(service, "/tmp/unused-editor-test")
        self.assertEqual(editor._attachment_links("https://dev-plato.pusan.ac.kr/mod/ubboard/article.php?id=1&bwid=2"), [{"url": image, "name": "notice.jpg"}])
        self.assertIn("img[src]", service.driver.execute_script.call_args.args[0])

    def service(self):
        service = WriteNoticeService.__new__(WriteNoticeService)
        service.driver = MagicMock()
        service.safe_get = MagicMock()
        return service

    def test_board_navigation_uses_selected_link_and_checks_login(self):
        service = self.service()
        link = service.driver.find_element.return_value
        link.get_attribute.return_value = "https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123"
        service.move_to_notice_board("취업 정보")
        service.safe_get.assert_called_once_with(link.get_attribute.return_value, login_required=True)
        link.click.assert_not_called()

    def test_missing_write_button_reloads_only_before_opening_form(self):
        service = self.service()
        service._notice_board_url = "https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123"
        button = object()
        with patch("together.plato.service.WebDriverWait") as wait:
            wait.return_value.until.side_effect = [TimeoutException(), button]
            self.assertIs(service._write_button(), button)
        service.safe_get.assert_called_once_with(service._notice_board_url, login_required=True)
        service.driver.execute_script.assert_not_called()

    def test_second_missing_button_stops_before_any_submission(self):
        service = self.service()
        service._notice_board_url = "https://dev-plato.pusan.ac.kr/mod/ubboard/view.php?id=123"
        with patch("together.plato.service.WebDriverWait") as wait:
            wait.return_value.until.side_effect = TimeoutException()
            with self.assertRaises(TimeoutException):
                service._write_button()
        self.assertEqual(service.safe_get.call_count, 1)
        service.driver.execute_script.assert_not_called()
