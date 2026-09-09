from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FashionClipboardTests(unittest.TestCase):
    def test_usb_mode_ignores_stale_wifi_address_and_requires_phone_sync(self):
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views import fashion_view

        controller = mock.Mock()
        controller.connect.return_value = {"address": "USB_SERIAL_123"}
        controller.copy_text_to_clipboard.side_effect = [
            {"phone_clipboard": True, "phone_clipboard_method": "uiautomator2"},
            {"phone_clipboard": True, "phone_clipboard_method": "uiautomator2"},
        ]
        settings = SimpleNamespace(
            address="192.168.110.4:44973",
            connection_mode="usb",
        )
        view = SimpleNamespace(config=SimpleNamespace())

        with mock.patch.object(
            fashion_view, "load_phone_control_settings", return_value=settings
        ), mock.patch.object(fashion_view, "PhoneController", return_value=controller):
            result = fashion_view.FashionView._copy_fashion_publish_data_to_phone(
                view,
                "Mô tả sản phẩm #aothun",
                "1731055146985293965",
            )

        controller.connect.assert_called_once_with("", connection_mode="usb")
        self.assertEqual(controller.copy_text_to_clipboard.call_count, 2)
        for call in controller.copy_text_to_clipboard.call_args_list:
            self.assertEqual(call.kwargs["address"], "USB_SERIAL_123")
            self.assertTrue(call.kwargs["sync_to_phone"])
            self.assertTrue(call.kwargs["require_phone_clipboard"])
        self.assertTrue(result["synced_to_phone"])
        self.assertEqual(result["address"], "USB_SERIAL_123")


if __name__ == "__main__":
    unittest.main()
