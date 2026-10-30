import unittest
from unittest.mock import MagicMock, patch
from together.notices.intelligence.post_editor import _click_filepicker_control


class FilepickerControls(unittest.TestCase):
    def test_legacy_dom_click_accepts_hidden_picker_control(self):
        driver, element = MagicMock(), MagicMock()
        element.is_displayed.return_value = False
        element.click.side_effect = AssertionError('Native click must not be used')
        with patch('together.notices.intelligence.post_editor.WebDriverWait', side_effect=AssertionError('Picker controls may be hidden')):
            _click_filepicker_control(driver, element)
        element.click.assert_not_called()
        driver.execute_script.assert_called_once_with('arguments[0].click();', element)

    def test_failed_dom_click_is_not_repeated(self):
        driver, element = MagicMock(), MagicMock()
        driver.execute_script.side_effect = RuntimeError('session ended')
        with self.assertRaises(RuntimeError):
            _click_filepicker_control(driver, element)
        self.assertEqual(driver.execute_script.call_count, 1)
