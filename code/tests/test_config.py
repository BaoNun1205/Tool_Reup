import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import os
import unittest
from unittest import mock

from auto_tiktok_editor.config import PipelineConfig


class PipelineConfigTests(unittest.TestCase):
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
        try:
            os.environ["AUTO_EDITOR_FFMPEG_BIN"] = "custom-ffmpeg"
            os.environ["AUTO_EDITOR_FFPROBE_BIN"] = "custom-ffprobe"
            os.environ["AUTO_EDITOR_YTDLP_BIN"] = "custom-ytdlp"
            os.environ["AUTO_EDITOR_LAZY_DOWN_ONLY"] = "false"
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
        with mock.patch.dict(os.environ, {"AUTO_EDITOR_TELEGRAM_BOT_TOKEN": ""}, clear=False):
            with mock.patch("auto_tiktok_editor.config.TELEGRAM_BOT_TOKEN_FILE") as token_file:
                token_file.exists.return_value = True
                token_file.read_text.return_value = "file-token\n"
                config = PipelineConfig.from_env()
        self.assertEqual(config.telegram_bot_token, "file-token")

    def _restore(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
