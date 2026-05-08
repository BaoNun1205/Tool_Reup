import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import json
import tempfile
import unittest
from unittest import mock

from auto_tiktok_editor.telegram_settings import (
    TelegramRuntimeSettings,
    clear_telegram_runtime_settings,
    load_telegram_runtime_settings,
    save_telegram_runtime_settings,
)


class TelegramSettingsTests(unittest.TestCase):
    def test_roundtrip_save_and_load_uses_dpapi_envelope(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            settings_path = Path(temp_dir.name) / "telegram_settings.json"
            sample = TelegramRuntimeSettings(bot_token="bot-token-demo", delivery_chat_id="123456")
            with mock.patch("auto_tiktok_editor.telegram_settings._settings_path", return_value=settings_path):
                with mock.patch("auto_tiktok_editor.telegram_settings._dpapi_protect", side_effect=lambda value: value):
                    with mock.patch("auto_tiktok_editor.telegram_settings._dpapi_unprotect", side_effect=lambda value: value):
                        save_telegram_runtime_settings(sample)
                        envelope = json.loads(settings_path.read_text(encoding="utf-8"))
                        loaded = load_telegram_runtime_settings()
            self.assertEqual(envelope["format"], "dpapi-v1")
            self.assertEqual(loaded, sample)
        finally:
            temp_dir.cleanup()

    def test_load_telegram_runtime_settings_supports_legacy_plaintext_payload(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            settings_path = Path(temp_dir.name) / "telegram_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "bot_token": "legacy-token",
                        "delivery_chat_id": "654321",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            with mock.patch("auto_tiktok_editor.telegram_settings._settings_path", return_value=settings_path):
                loaded = load_telegram_runtime_settings()
            self.assertEqual(loaded.bot_token, "legacy-token")
            self.assertEqual(loaded.delivery_chat_id, "654321")
        finally:
            temp_dir.cleanup()

    def test_clear_telegram_runtime_settings_removes_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            settings_path = Path(temp_dir.name) / "telegram_settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            with mock.patch("auto_tiktok_editor.telegram_settings._settings_path", return_value=settings_path):
                clear_telegram_runtime_settings()
            self.assertFalse(settings_path.exists())
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
