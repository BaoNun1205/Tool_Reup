import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import tempfile
import unittest
import json
from unittest import mock

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

    def test_build_lazy_down_command_uses_json_manifest_mode(self):
        command = self.downloader._build_lazy_down_command(
            "https://www.tiktok.com/@store/video/123",
            self.base_dir,
        )
        self.assertIn(self.downloader.config.lazy_down_bin, command)
        self.assertIn("--write-json", command)
        self.assertIn("--output-file", command)
        self.assertIn("--all", command)
        self.assertIn("--quiet", command)

    def test_normalize_source_url_for_lazy_down_keeps_full_url(self):
        source_url = "https://www.tiktok.com/@store/video/123"
        self.assertEqual(self.downloader._normalize_source_url_for_lazy_down(source_url), source_url)

    def test_normalize_source_url_for_lazy_down_strips_tracking_query(self):
        source_url = "https://www.tiktok.com/@store/video/123?_r=1&_t=ZS-abc"
        self.assertEqual(
            self.downloader._normalize_source_url_for_lazy_down(source_url),
            "https://www.tiktok.com/@store/video/123",
        )

    def test_normalize_source_url_for_lazy_down_expands_shortlink(self):
        class FakeResponse(object):
            def __init__(self, resolved_url):
                self._resolved_url = resolved_url

            def geturl(self):
                return self._resolved_url

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        source_url = "https://vt.tiktok.com/ZSHuwkq4J/"
        resolved_url = "https://www.tiktok.com/@store/video/1234567890?_r=1&_t=ZS-abc"
        with mock.patch("auto_tiktok_editor.media.downloader.urlopen", return_value=FakeResponse(resolved_url)):
            self.assertEqual(
                self.downloader._normalize_source_url_for_lazy_down(source_url),
                "https://www.tiktok.com/@store/video/1234567890",
            )

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

    def test_select_lazy_down_video_prefers_hd_no_watermark(self):
        hd_path = self.base_dir / "video_hd.mp4"
        normal_path = self.base_dir / "video_normal.mp4"
        hd_path.write_text("hd", encoding="utf-8")
        normal_path.write_text("normal", encoding="utf-8")
        payload = {
            "medias": [
                {
                    "type": "video",
                    "quality": "no_watermark",
                    "width": 576,
                    "height": 1024,
                    "filesize": 9000,
                    "localPath": str(normal_path),
                },
                {
                    "type": "video",
                    "quality": "hd_no_watermark",
                    "width": 720,
                    "height": 1280,
                    "filesize": 5000,
                    "localPath": str(hd_path),
                },
            ]
        }
        selected_path, selected_media = self.downloader._select_lazy_down_video(payload, self.base_dir)
        self.assertEqual(selected_path, hd_path.resolve())
        self.assertEqual(selected_media.get("quality"), "hd_no_watermark")

    def test_find_lazy_down_json_path_reads_json_path_from_stdout(self):
        manifest_path = self.base_dir / "lazy_result_tiktok_123.json"
        manifest_path.write_text(json.dumps({"medias": []}), encoding="utf-8")
        command_output = json.dumps({"jsonPath": str(manifest_path)})
        result = self.downloader._find_lazy_down_json_path(command_output, self.base_dir)
        self.assertEqual(result, manifest_path.resolve())


if __name__ == "__main__":
    unittest.main()
