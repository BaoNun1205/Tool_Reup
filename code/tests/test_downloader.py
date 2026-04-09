import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import tempfile
import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.exceptions import DownloadError
from auto_tiktok_editor.media.downloader import SourceDownloader
from auto_tiktok_editor.utils.command import CommandRunner


class SourceDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.downloader = SourceDownloader(PipelineConfig(), CommandRunner())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_downloaded_file_prefers_printed_after_move_video_path(self):
        audio_path = self.base_dir / "source.m4a"
        audio_path.write_text("audio", encoding="utf-8")
        video_path = self.base_dir / "source.mp4"
        video_path.write_text("video", encoding="utf-8")
        result = self.downloader._resolve_downloaded_file(str(video_path), self.base_dir)
        self.assertEqual(result, video_path.resolve())

    def test_resolve_downloaded_file_ignores_printed_audio_path_and_falls_back_to_mp4(self):
        audio_path = self.base_dir / "source.m4a"
        audio_path.write_text("audio", encoding="utf-8")
        video_path = self.base_dir / "source.mp4"
        video_path.write_text("video", encoding="utf-8")
        result = self.downloader._resolve_downloaded_file(str(audio_path), self.base_dir)
        self.assertEqual(result, video_path)

    def test_resolve_downloaded_file_falls_back_to_explicit_mp4(self):
        (self.base_dir / "source.m4a").write_text("audio", encoding="utf-8")
        video_path = self.base_dir / "source.mp4"
        video_path.write_text("video", encoding="utf-8")
        result = self.downloader._resolve_downloaded_file("", self.base_dir)
        self.assertEqual(result, video_path)

    def test_resolve_downloaded_file_rejects_audio_only_outputs(self):
        audio_path = self.base_dir / "source.m4a"
        audio_path.write_text("audio", encoding="utf-8")
        with self.assertRaises(DownloadError):
            self.downloader._resolve_downloaded_file(str(audio_path), self.base_dir)

    def test_build_base_command_uses_chrome_impersonation(self):
        command = self.downloader._build_base_command(
            "https://vt.tiktok.com/example/",
            str(self.base_dir / "source.%(ext)s"),
            impersonate=True,
        )
        self.assertIn("--impersonate", command)
        self.assertIn("chrome", command)
        self.assertIn("--user-agent", command)
        self.assertIn(self.downloader.config.tiktok_web_user_agent, command)

    def test_build_browser_cookie_command_exports_browser_cookies(self):
        command = self.downloader._build_browser_cookie_command(
            "https://www.tiktok.com/@store/video/123",
            str(self.base_dir / "source.%(ext)s"),
            "chrome",
            self.base_dir / "browser_chrome_cookies.txt",
        )
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("chrome", command)
        self.assertIn("--cookies", command)
        self.assertIn(str(self.base_dir / "browser_chrome_cookies.txt"), command)

    def test_stage_cookies_file_copies_into_workspace(self):
        cookies_path = self.base_dir / "cookies.txt"
        cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        workspace_dir = self.base_dir / "workspace"
        staged = self.downloader._stage_cookies_file(cookies_path, workspace_dir)
        self.assertEqual(staged, workspace_dir / "runtime_cookies.txt")
        self.assertTrue(staged.exists())
        self.assertEqual(staged.read_text(encoding="utf-8"), "# Netscape HTTP Cookie File\n")

    def test_format_failure_message_calls_out_cookie_403(self):
        message = self.downloader._format_failure_message(
            [
                "cookies_file: Command failed (1): ERROR: [TikTok] 123: Unable to download webpage: HTTP Error 403: Forbidden",
                "direct: yt-dlp downloaded non-video artifacts only; the TikTok URL may resolve to audio-only media, an image post, or an unsupported asset.",
            ],
            has_custom_cookies=True,
        )
        self.assertIn("returned HTTP 403", message)
        self.assertIn("stale", message)

    def test_format_failure_message_calls_out_fresh_browser_session_for_browser_cookie_failures(self):
        message = self.downloader._format_failure_message(
            [
                "direct: yt-dlp downloaded non-video artifacts only; the TikTok URL may resolve to audio-only media, an image post, or an unsupported asset.",
                "cookies_chrome_fresh: Command failed (1): ERROR: Could not copy Chrome cookie database.",
            ],
            has_custom_cookies=False,
        )
        self.assertIn("Close Chrome and Edge completely", message)
        self.assertIn("User-Agent", message)


if __name__ == "__main__":
    unittest.main()
