import unittest
from unittest.mock import MagicMock, patch
from selenium.common.exceptions import ElementClickInterceptedException
from together.notices.intelligence.post_editor import _click_filepicker_control


class FilepickerControls(unittest.TestCase):
    def test_scrolls_below_fixed_header_before_native_click(self):
        driver, element = MagicMock(), MagicMock()
        with patch('together.notices.intelligence.post_editor.WebDriverWait'):
            _click_filepicker_control(driver, element)
        self.assertIn("block: 'center'", driver.execute_script.call_args_list[0].args[0])
        element.click.assert_called_once_with()
        self.assertEqual(driver.execute_script.call_count, 1)

    def test_intercepted_native_click_uses_single_dom_click(self):
        driver, element = MagicMock(), MagicMock()
        element.click.side_effect = ElementClickInterceptedException('fixed navigation')
        with patch('together.notices.intelligence.post_editor.WebDriverWait'):
            _click_filepicker_control(driver, element)
        element.click.assert_called_once_with()
        self.assertEqual(driver.execute_script.call_count, 2)
        driver.execute_script.assert_called_with('arguments[0].click();', element)

    def test_unrelated_failure_is_not_retried_as_another_click(self):
        driver, element = MagicMock(), MagicMock()
        element.click.side_effect = RuntimeError('session ended')
        with patch('together.notices.intelligence.post_editor.WebDriverWait'):
            with self.assertRaises(RuntimeError):
                _click_filepicker_control(driver, element)
        self.assertEqual(driver.execute_script.call_count, 1)
