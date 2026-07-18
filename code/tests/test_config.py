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
            "AUTO_EDITOR_BACKGROUND_REMOVAL_BACKEND",
            "AUTO_EDITOR_BACKGROUNDREMOVER_BIN",
            "AUTO_EDITOR_BACKGROUNDREMOVER_MODEL",
            "AUTO_EDITOR_REMBG_MODEL",
            "AUTO_EDITOR_REMBG_PROVIDERS",
            "AUTO_EDITOR_REMBG_POST_PROCESS_MASK",
            "AUTO_EDITOR_REMBG_MASK_EXPAND_PIXELS",
            "AUTO_EDITOR_ADB_BIN",
            "AUTO_EDITOR_SCRCPY_BIN",
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
            os.environ["AUTO_EDITOR_BACKGROUND_REMOVAL_BACKEND"] = "backgroundremover"
            os.environ["AUTO_EDITOR_BACKGROUNDREMOVER_BIN"] = "C:/custom/backgroundremover.exe"
            os.environ["AUTO_EDITOR_BACKGROUNDREMOVER_MODEL"] = "u2net"
            os.environ["AUTO_EDITOR_REMBG_MODEL"] = "silueta"
            os.environ["AUTO_EDITOR_REMBG_PROVIDERS"] = "directml,cpu"
            os.environ["AUTO_EDITOR_REMBG_POST_PROCESS_MASK"] = "true"
            os.environ["AUTO_EDITOR_REMBG_MASK_EXPAND_PIXELS"] = "5"
            os.environ["AUTO_EDITOR_ADB_BIN"] = "C:/custom/adb.exe"
            os.environ["AUTO_EDITOR_SCRCPY_BIN"] = "D:/custom/scrcpy.exe"

            config = PipelineConfig.from_env()

            self.assertEqual(config.video_cut_mode, "scene")
            self.assertAlmostEqual(config.fixed_chunk_duration_seconds, 2.5)
            self.assertAlmostEqual(config.scene_threshold, 0.42)
            self.assertEqual(config.product_image_crop_ratio, "4:3")
            self.assertEqual(config.product_image_motion, "zoom")
            self.assertEqual(config.background_removal_backend, "backgroundremover")
            self.assertEqual(config.backgroundremover_bin, "C:/custom/backgroundremover.exe")
            self.assertEqual(config.backgroundremover_model, "u2net")
            self.assertEqual(config.rembg_model, "silueta")
            self.assertEqual(config.rembg_providers, ("DmlExecutionProvider", "CPUExecutionProvider"))
            self.assertTrue(config.rembg_post_process_mask)
            self.assertEqual(config.rembg_mask_expand_pixels, 5)
            self.assertEqual(config.telegram_bot_token, "bot-token")
            self.assertEqual(config.telegram_allowed_chat_ids, (123, 456))
            self.assertEqual(config.telegram_delivery_chat_id, "987654321")
            self.assertEqual(config.adb_bin, "C:/custom/adb.exe")
            self.assertEqual(config.scrcpy_bin, "D:/custom/scrcpy.exe")
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

    def test_from_env_accepts_remove_background_video_cut_mode(self):
        with mock.patch.dict(os.environ, {"AUTO_EDITOR_VIDEO_CUT_MODE": "remove_background"}, clear=False):
            config = PipelineConfig.from_env()

        self.assertEqual(config.video_cut_mode, "remove_background")

    def test_from_env_defaults_remove_background_to_rembg_directml_isnet(self):
        with mock.patch.dict(
            os.environ,
            {
                "AUTO_EDITOR_BACKGROUND_REMOVAL_BACKEND": "",
                "AUTO_EDITOR_REMBG_MODEL": "",
                "AUTO_EDITOR_REMBG_PROVIDERS": "",
                "AUTO_EDITOR_REMBG_POST_PROCESS_MASK": "",
                "AUTO_EDITOR_REMBG_MASK_EXPAND_PIXELS": "",
            },
            clear=False,
        ):
            config = PipelineConfig.from_env()

        self.assertEqual(config.background_removal_backend, "rembg")
        self.assertEqual(config.rembg_model, "isnet-general-use")
        self.assertEqual(config.rembg_providers, ("DmlExecutionProvider", "CPUExecutionProvider"))
        self.assertFalse(config.rembg_post_process_mask)
        self.assertEqual(config.rembg_mask_expand_pixels, 3)
        self.assertEqual(config.backgroundremover_model, "u2netp")

    def test_build_ids_have_expected_prefixes(self):
        config = PipelineConfig()

        self.assertIn("_", config.build_job_id())
        self.assertTrue(config.build_session_id().startswith("session_"))

    def test_resolve_project_root_finds_existing_data_above_runtime_dist(self):
        import auto_tiktok_editor.config as config_module

        project_root = Path("D:/Tool_Reup/code")
        runtime_root = project_root / "build" / "profile-manager" / "cli.dist"

        def fake_exists(path):
            normalized = Path(path).as_posix()
            return normalized == "D:/Tool_Reup/code/tiktok_profile_manager.sqlite3"

        with mock.patch.dict(os.environ, {"AUTO_EDITOR_PROJECT_ROOT": ""}, clear=False), mock.patch.object(
            config_module,
            "_runtime_root",
            return_value=runtime_root,
        ), mock.patch.object(Path, "exists", fake_exists):
            self.assertEqual(config_module._resolve_project_root(), project_root)

    def test_resolve_project_root_env_override_wins(self):
        import auto_tiktok_editor.config as config_module

        configured_root = Path("D:/Tool_Reup/code")
        with mock.patch.dict(os.environ, {"AUTO_EDITOR_PROJECT_ROOT": str(configured_root)}, clear=False):
            self.assertEqual(config_module._resolve_project_root(), configured_root.resolve())


if __name__ == "__main__":
    unittest.main()
