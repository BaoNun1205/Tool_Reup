from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from auto_tiktok_editor.app.media_cleanup import (
    CleanupItemInfo,
    GranularCleanupReport,
    execute_granular_cleanup,
    format_granular_cleanup_report,
    scan_cleanup_items,
)
from auto_tiktok_editor.config import PipelineConfig


class GranularCleanupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

        self.output_root = self.base_dir / "output"
        self.telegram_input_root = self.output_root / "_telegram_inputs"
        self.tmp_dir = self.base_dir / "tmp"
        self.clips_dir = self.base_dir / "clips"
        self.screenshots_dir = self.base_dir / "phone_screenshots"
        self.logs_dir = self.base_dir / "logs"
        self.queue_dir = self.base_dir / "profile_video_queue"
        self.profiles_dir = self.base_dir / "profiles"
        self.build_dir = self.base_dir / "build"
        self.backup_dir = self.base_dir / "data_backup_before_rebuild_20260803"

        for d in [
            self.output_root,
            self.telegram_input_root,
            self.tmp_dir,
            self.clips_dir,
            self.screenshots_dir,
            self.logs_dir,
            self.queue_dir,
            self.profiles_dir,
            self.build_dir,
            self.backup_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        self.config = PipelineConfig(
            default_output_root=self.output_root,
            telegram_input_root=self.telegram_input_root,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scan_and_granular_cleanup_selective(self) -> None:
        # Create test files
        tmp_file = self.tmp_dir / "render.tmp"
        tmp_file.write_text("temporary data", encoding="utf-8")

        tg_input_file = self.telegram_input_root / "photo.jpg"
        tg_input_file.write_text("photo data", encoding="utf-8")

        log_file = self.logs_dir / "bot.log"
        log_file.write_text("log data", encoding="utf-8")

        session_dir = self.output_root / "session_001"
        session_dir.mkdir(parents=True, exist_ok=True)
        final_video = session_dir / "final_video.mp4"
        final_video.write_text("video final data", encoding="utf-8")

        # Create browser profile with cache and protected cookie
        prof_cache_dir = self.profiles_dir / "acc1" / "Default" / "Cache"
        prof_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = prof_cache_dir / "data_0"
        cache_file.write_text("cache data", encoding="utf-8")

        prof_cookie_dir = self.profiles_dir / "acc1" / "Default"
        cookie_file = prof_cookie_dir / "Cookies"
        cookie_file.write_text("secret session cookies", encoding="utf-8")

        # 1. Test scanning
        items = scan_cleanup_items(self.config, self.base_dir)
        item_keys = {it.key for it in items}
        self.assertIn("tmp", item_keys)
        self.assertIn("logs", item_keys)
        self.assertIn("telegram_inputs", item_keys)
        self.assertIn("browser_cache", item_keys)
        self.assertIn("output_videos", item_keys)

        # 2. Test selective cleanup: clean safe items ONLY (keep output_videos)
        selected_safe = ["tmp", "logs", "telegram_inputs", "browser_cache"]
        report = execute_granular_cleanup(
            selected_keys=selected_safe,
            config=self.config,
            project_root=self.base_dir,
        )

        self.assertFalse(tmp_file.exists(), "Tmp file should be deleted")
        self.assertFalse(tg_input_file.exists(), "Telegram input file should be deleted")
        self.assertFalse(log_file.exists(), "Log file should be deleted")
        self.assertFalse(cache_file.exists(), "Browser cache file should be deleted")

        # CRITICAL VERIFICATIONS:
        self.assertTrue(final_video.exists(), "Final video MUST BE PRESERVED when output_videos is not selected!")
        self.assertTrue(cookie_file.exists(), "Browser cookies MUST NEVER BE DELETED!")

        # 3. Test format report
        msg = format_granular_cleanup_report(report)
        self.assertTrue(len(msg) > 0)
        self.assertIn("Đã xóa", msg)


if __name__ == "__main__":
    unittest.main()
