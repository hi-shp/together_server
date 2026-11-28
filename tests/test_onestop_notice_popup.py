import unittest
from unittest.mock import MagicMock
from selenium.webdriver.remote.webelement import WebElement
from together.crawler.onestop import dismiss_sso_notices


class OnestopNoticePopupTests(unittest.TestCase):
    def test_visible_sso_announcement_uses_its_native_close_control(self):
        popup = MagicMock(spec=WebElement)
        popup.is_displayed.side_effect = [True, False]
        notice_link = MagicMock(text='점검 안내')
        close = MagicMock(text='X 닫기')
        popup.find_elements.return_value = [notice_link, close]
        driver = MagicMock()
        driver.find_elements.return_value = [popup]
        dismiss_sso_notices(driver, 1)
        notice_link.click.assert_not_called()
        close.click.assert_called_once_with()
        popup.find_elements.assert_called_once_with('css selector', 'a.n-btn-label')

    def test_hidden_notices_and_other_controls_are_left_alone(self):
        for visible, caption in [(False, 'X 닫기'), (True, '동의하고 계속'), (True, '오늘 하루 닫기')]:
            with self.subTest(visible=visible, caption=caption):
                popup = MagicMock(spec=WebElement)
                popup.is_displayed.return_value = visible
                control = MagicMock(text=caption)
                popup.find_elements.return_value = [control]
                driver = MagicMock()
                driver.find_elements.return_value = [popup]
                dismiss_sso_notices(driver, 1)
                control.click.assert_not_called()
