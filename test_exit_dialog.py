"""只使用虚构窗口验证关闭守卫，不启动主程序或读取题库。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import exit_dialog


class ExitConfirmationTests(unittest.TestCase):
    def test_cancel_keeps_window_and_confirm_allows_close(self):
        owner = object()
        guard = exit_dialog.ExitConfirmation(SimpleNamespace(native=owner))
        with patch.object(exit_dialog, "confirm_exit", side_effect=[False, True]) as confirm:
            self.assertFalse(guard())
            self.assertTrue(guard())
        self.assertEqual(confirm.call_count, 2)
        confirm.assert_called_with(owner)

    def test_reentrant_close_does_not_open_a_second_dialog(self):
        guard = exit_dialog.ExitConfirmation(SimpleNamespace(native=None))

        def confirm(_owner):
            self.assertFalse(guard())
            return True

        with patch.object(exit_dialog, "confirm_exit", side_effect=confirm) as show:
            self.assertTrue(guard())
        show.assert_called_once()

    def test_failure_keeps_window_and_releases_guard_for_retry(self):
        guard = exit_dialog.ExitConfirmation(SimpleNamespace(native=None))
        with patch.object(exit_dialog, "confirm_exit", side_effect=[RuntimeError("fake failure"), True]):
            self.assertFalse(guard())
            self.assertTrue(guard())

    def test_native_dialog_failure_falls_back_to_default_no(self):
        with (
            patch.object(exit_dialog, "build_exit_dialog", side_effect=RuntimeError("fake GUI failure")),
            patch("ctypes.windll.user32.MessageBoxW", return_value=7) as message,
        ):
            self.assertFalse(exit_dialog.confirm_exit())
        self.assertEqual(message.call_args.args[-1] & 0x100, 0x100)


if __name__ == "__main__":
    unittest.main()
