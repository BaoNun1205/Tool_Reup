import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import os
import tempfile
import unittest
from unittest import mock

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.commercial_entry import COMMERCIAL_LICENSE_SERVER_URL
from auto_tiktok_editor.telegram_settings import TelegramRuntimeSettings
from auto_tiktok_editor.license.config import LicenseClientConfig


class PipelineConfigTests(unittest.TestCase):
    def test_license_client_config_uses_localhost_in_local_mode(self):
        previous_url = os.environ.get("AUTO_EDITOR_LICENSE_SERVER_URL")
        previous_mode = os.environ.get("AUTO_EDITOR_COMMERCIAL_MODE")
        try:
            os.environ.pop("AUTO_EDITOR_LICENSE_SERVER_URL", None)
            os.environ["AUTO_EDITOR_COMMERCIAL_MODE"] = "0"
            config = LicenseClientConfig.from_env()
            self.assertEqual(config.server_base_url, "http://127.0.0.1:8787")
        finally:
            self._restore("AUTO_EDITOR_LICENSE_SERVER_URL", previous_url)
            self._restore("AUTO_EDITOR_COMMERCIAL_MODE", previous_mode)

    def test_commercial_license_server_url_default_can_be_applied(self):
        previous = os.environ.get("AUTO_EDITOR_LICENSE_SERVER_URL")
        previous_mode = os.environ.get("AUTO_EDITOR_COMMERCIAL_MODE")
        try:
            os.environ.pop("AUTO_EDITOR_LICENSE_SERVER_URL", None)
            os.environ["AUTO_EDITOR_COMMERCIAL_MODE"] = "1"
            os.environ.setdefault("AUTO_EDITOR_LICENSE_SERVER_URL", COMMERCIAL_LICENSE_SERVER_URL)
            config = LicenseClientConfig.from_env()
            self.assertEqual(config.server_base_url, COMMERCIAL_LICENSE_SERVER_URL)
        finally:
            self._restore("AUTO_EDITOR_LICENSE_SERVER_URL", previous)
            self._restore("AUTO_EDITOR_COMMERCIAL_MODE", previous_mode)

    def test_commercial_license_client_config_requires_online_server_url(self):
        previous_url = os.environ.get("AUTO_EDITOR_LICENSE_SERVER_URL")
        previous_mode = os.environ.get("AUTO_EDITOR_COMMERCIAL_MODE")
        try:
            os.environ.pop("AUTO_EDITOR_LICENSE_SERVER_URL", None)
            os.environ["AUTO_EDITOR_COMMERCIAL_MODE"] = "1"
            with self.assertRaises(ValueError):
                LicenseClientConfig.from_env()
        finally:
            self._restore("AUTO_EDITOR_LICENSE_SERVER_URL", previous_url)
            self._restore("AUTO_EDITOR_COMMERCIAL_MODE", previous_mode)

    def test_commercial_license_client_config_rejects_localhost_url(self):
        previous_url = os.environ.get("AUTO_EDITOR_LICENSE_SERVER_URL")
        previous_mode = os.environ.get("AUTO_EDITOR_COMMERCIAL_MODE")
        try:
            os.environ["AUTO_EDITOR_LICENSE_SERVER_URL"] = "http://127.0.0.1:8787"
            os.environ["AUTO_EDITOR_COMMERCIAL_MODE"] = "1"
            with self.assertRaises(ValueError):
                LicenseClientConfig.from_env()
        finally:
            self._restore("AUTO_EDITOR_LICENSE_SERVER_URL", previous_url)
            self._restore("AUTO_EDITOR_COMMERCIAL_MODE", previous_mode)

    def test_from_env_prefers_bundled_runtime_tools_when_frozen(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            tools_dir = Path(temp_dir.name) / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            bundled = {
                "tools/ffmpeg.exe": str(tools_dir / "ffmpeg.exe"),
                "tools/ffprobe.exe": str(tools_dir / "ffprobe.exe"),
                "tools/yt-dlp.exe": str(tools_dir / "yt-dlp.exe"),
                "tools/lazy-down.cmd": str(tools_dir / "lazy-down.cmd"),
            }
            with mock.patch.dict(
                os.environ,
                {
                    "AUTO_EDITOR_FFMPEG_BIN": "",
                    "AUTO_EDITOR_FFPROBE_BIN": "",
                    "AUTO_EDITOR_YTDLP_BIN": "",
                    "AUTO_EDITOR_LAZY_DOWN_BIN": "",
                },
                clear=False,
            ):
                with mock.patch("auto_tiktok_editor.config._find_runtime_binary", side_effect=lambda relative: bundled.get(relative)):
                    config = PipelineConfig.from_env()
            self.assertEqual(config.ffmpeg_bin, bundled["tools/ffmpeg.exe"])
            self.assertEqual(config.ffprobe_bin, bundled["tools/ffprobe.exe"])
            self.assertEqual(config.ytdlp_bin, bundled["tools/yt-dlp.exe"])
            self.assertEqual(config.lazy_down_bin, bundled["tools/lazy-down.cmd"])
        finally:
            temp_dir.cleanup()

    def test_from_env_overrides_binary_names(self):
        old_ffmpeg = os.environ.get("AUTO_EDITOR_FFMPEG_BIN")
        old_ffprobe = os.environ.get("AUTO_EDITOR_FFPROBE_BIN")
        old_ytdlp = os.environ.get("AUTO_EDITOR_YTDLP_BIN")
        old_lazy_only = os.environ.get("AUTO_EDITOR_LAZY_DOWN_ONLY")
        old_telegram_token = os.environ.get("AUTO_EDITOR_TELEGRAM_BOT_TOKEN")
        old_telegram_timeout = os.environ.get("AUTO_EDITOR_TELEGRAM_POLL_TIMEOUT_SECONDS")
        old_telegram_interval = os.environ.get("AUTO_EDITOR_TELEGRAM_POLL_INTERVAL_SECONDS")
        old_telegram_chat_ids = os.environ.get("AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS")
        old_telegram_delivery_chat_id = os.environ.get("AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID")
        old_allow_local_telegram = os.environ.get("AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM")
        try:
            os.environ["AUTO_EDITOR_FFMPEG_BIN"] = "custom-ffmpeg"
            os.environ["AUTO_EDITOR_FFPROBE_BIN"] = "custom-ffprobe"
            os.environ["AUTO_EDITOR_YTDLP_BIN"] = "custom-ytdlp"
            os.environ["AUTO_EDITOR_LAZY_DOWN_ONLY"] = "false"
            os.environ["AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM"] = "true"
            os.environ["AUTO_EDITOR_TELEGRAM_BOT_TOKEN"] = "bot-token"
            os.environ["AUTO_EDITOR_TELEGRAM_POLL_TIMEOUT_SECONDS"] = "55"
            os.environ["AUTO_EDITOR_TELEGRAM_POLL_INTERVAL_SECONDS"] = "4"
            os.environ["AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS"] = "123, 456,invalid"
            os.environ["AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID"] = "987654321"
            config = PipelineConfig.from_env()
            self.assertEqual(config.ffmpeg_bin, "custom-ffmpeg")
            self.assertEqual(config.ffprobe_bin, "custom-ffprobe")
            self.assertEqual(config.ytdlp_bin, "custom-ytdlp")
            self.assertFalse(config.download_via_lazy_down_only)
            self.assertEqual(config.telegram_bot_token, "bot-token")
            self.assertEqual(config.telegram_poll_timeout_seconds, 55)
            self.assertEqual(config.telegram_poll_interval_seconds, 4)
            self.assertEqual(config.telegram_allowed_chat_ids, (123, 456))
            self.assertEqual(config.telegram_delivery_chat_id, "987654321")
        finally:
            self._restore("AUTO_EDITOR_FFMPEG_BIN", old_ffmpeg)
            self._restore("AUTO_EDITOR_FFPROBE_BIN", old_ffprobe)
            self._restore("AUTO_EDITOR_YTDLP_BIN", old_ytdlp)
            self._restore("AUTO_EDITOR_LAZY_DOWN_ONLY", old_lazy_only)
            self._restore("AUTO_EDITOR_TELEGRAM_BOT_TOKEN", old_telegram_token)
            self._restore("AUTO_EDITOR_TELEGRAM_POLL_TIMEOUT_SECONDS", old_telegram_timeout)
            self._restore("AUTO_EDITOR_TELEGRAM_POLL_INTERVAL_SECONDS", old_telegram_interval)
            self._restore("AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS", old_telegram_chat_ids)
            self._restore("AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID", old_telegram_delivery_chat_id)
            self._restore("AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM", old_allow_local_telegram)

    def test_build_job_id_has_prefix_and_suffix(self):
        config = PipelineConfig()
        job_id = config.build_job_id()
        self.assertIn("_", job_id)
        self.assertGreaterEqual(len(job_id), 10)

    def test_build_session_id_has_session_prefix(self):
        config = PipelineConfig()
        session_id = config.build_session_id()
        self.assertTrue(session_id.startswith("session_"))
        self.assertGreaterEqual(len(session_id), 18)

    def test_from_env_falls_back_to_local_telegram_token_file(self):
        with mock.patch.dict(
            os.environ,
            {"AUTO_EDITOR_TELEGRAM_BOT_TOKEN": "", "AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM": "true"},
            clear=False,
        ):
            with mock.patch("auto_tiktok_editor.config.TELEGRAM_BOT_TOKEN_FILE") as token_file:
                token_file.exists.return_value = True
                token_file.read_text.return_value = "file-token\n"
                config = PipelineConfig.from_env()
        self.assertEqual(config.telegram_bot_token, "file-token")

    def test_from_env_loads_saved_telegram_runtime_settings(self):
        with mock.patch.dict(
            os.environ,
            {
                "AUTO_EDITOR_TELEGRAM_BOT_TOKEN": "",
                "AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID": "",
                "AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS": "",
                "AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM": "false",
            },
            clear=False,
        ):
            with mock.patch("auto_tiktok_editor.config._resolve_telegram_bot_token", return_value=""), mock.patch(
                "auto_tiktok_editor.config.load_telegram_runtime_settings",
                return_value=TelegramRuntimeSettings(bot_token="saved-token", delivery_chat_id="123456"),
            ):
                config = PipelineConfig.from_env()
        self.assertTrue(config.allow_local_telegram)
        self.assertEqual(config.telegram_bot_token, "saved-token")
        self.assertEqual(config.telegram_delivery_chat_id, "123456")
        self.assertEqual(config.telegram_allowed_chat_ids, (123456,))

    def _restore(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
