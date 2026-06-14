import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.telegram_settings import TelegramRuntimeSettings


class PipelineConfigTests(unittest.TestCase):
    def test_from_env_overrides_profile_manager_settings(self):
        old_values = {name: os.environ.get(name) for name in (
            "AUTO_EDITOR_OUTPUT_ROOT",
            "AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM",
            "AUTO_EDITOR_TELEGRAM_BOT_TOKEN",
            "AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS",
            "AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID",
            "AUTO_EDITOR_VIDEO_CUT_MODE",
            "AUTO_EDITOR_FIXED_CHUNK_DURATION_SECONDS",
            "AUTO_EDITOR_SCENE_THRESHOLD",
            "AUTO_EDITOR_PRODUCT_IMAGE_CROP_RATIO",
            "AUTO_EDITOR_PRODUCT_IMAGE_MOTION",
        )}
        try:
            os.environ["AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM"] = "true"
            os.environ["AUTO_EDITOR_TELEGRAM_BOT_TOKEN"] = "bot-token"
            os.environ["AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS"] = "123, 456,invalid"
            os.environ["AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID"] = "987654321"
            os.environ["AUTO_EDITOR_VIDEO_CUT_MODE"] = "scene"
            os.environ["AUTO_EDITOR_FIXED_CHUNK_DURATION_SECONDS"] = "2.5"
            os.environ["AUTO_EDITOR_SCENE_THRESHOLD"] = "0.42"
            os.environ["AUTO_EDITOR_PRODUCT_IMAGE_CROP_RATIO"] = "4:3"
            os.environ["AUTO_EDITOR_PRODUCT_IMAGE_MOTION"] = "zoom"

            config = PipelineConfig.from_env()

            self.assertEqual(config.video_cut_mode, "scene")
            self.assertAlmostEqual(config.fixed_chunk_duration_seconds, 2.5)
            self.assertAlmostEqual(config.scene_threshold, 0.42)
            self.assertEqual(config.product_image_crop_ratio, "4:3")
            self.assertEqual(config.product_image_motion, "zoom")
            self.assertEqual(config.telegram_bot_token, "bot-token")
            self.assertEqual(config.telegram_allowed_chat_ids, (123, 456))
            self.assertEqual(config.telegram_delivery_chat_id, "987654321")
        finally:
            for name, value in old_values.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

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

    def test_build_ids_have_expected_prefixes(self):
        config = PipelineConfig()

        self.assertIn("_", config.build_job_id())
        self.assertTrue(config.build_session_id().startswith("session_"))


if __name__ == "__main__":
    unittest.main()
