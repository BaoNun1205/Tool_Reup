import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest
import queue
import tempfile
from pathlib import Path
from unittest import mock

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.ui.app import EditorApplication


class FakeVar(object):
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeButton(object):
    def __init__(self):
        self.state = None

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class EditorApplicationTests(unittest.TestCase):
    def test_starts_embedded_telegram_bot_when_token_is_configured(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig(telegram_bot_token="token")
        app.logger = mock.Mock()
        app.telegram_bot_service = None
        app.telegram_bot_thread = None
        logged_messages = []
        app._append_log = lambda message, reset=False: logged_messages.append(message)

        fake_thread = mock.Mock()
        fake_thread.start = mock.Mock()
        fake_service = mock.Mock()

        with mock.patch("auto_tiktok_editor.ui.app.TelegramBotService", return_value=fake_service):
            with mock.patch("auto_tiktok_editor.ui.app.threading.Thread", return_value=fake_thread):
                app._start_embedded_telegram_bot_if_configured()

        self.assertIs(app.telegram_bot_service, fake_service)
        self.assertIs(app.telegram_bot_thread, fake_thread)
        fake_thread.start.assert_called_once()
        self.assertTrue(any("Bot Telegram nền" in message for message in logged_messages))

    def test_blur_percent_defaults_to_config_fade_ratio(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig(split_separator_fade_ratio=0.50)

        self.assertEqual(app._default_blur_percent(), 50)

    def test_fade_ratio_mapping_matches_slider_percent(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()

        self.assertAlmostEqual(app._fade_ratio_from_blur_percent(40), 0.40)
        self.assertAlmostEqual(app._fade_ratio_from_blur_percent(95), 0.95)
        self.assertAlmostEqual(app._fade_ratio_from_blur_percent(5), 0.05)

    def test_rerun_row_queues_live_request_while_session_is_running(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()
        app.running = True
        app.latest_result = None
        app.session_rerun_queue = queue.Queue()
        app._set_row_status = lambda *args, **kwargs: None
        app._set_session_status = lambda *args, **kwargs: None
        app._append_log = lambda *args, **kwargs: None

        row = type(
            "Row",
            (),
            {
                "row_id": "row_001",
                "url_var": FakeVar("https://www.tiktok.com/@store/video/1234567890"),
                "image_var": FakeVar(str(Path("d:/Tool_Reup/code/tests/fixtures/product.png"))),
                "opacity_var": FakeVar(40),
                "rerun_button": FakeButton(),
            },
        )()
        app.rows = [row]

        app._rerun_row("row_001")

        queued_index, queued_spec = app.session_rerun_queue.get_nowait()
        self.assertEqual(queued_index, 0)
        self.assertEqual(queued_spec.row_id, "row_001")
        self.assertEqual(queued_spec.source_video_url, "https://www.tiktok.com/@store/video/1234567890")
        self.assertEqual(queued_spec.product_image, Path("d:/Tool_Reup/code/tests/fixtures/product.png"))
        self.assertAlmostEqual(queued_spec.overlay_alpha_ratio, 0.40)
        self.assertEqual(row.rerun_button.state, "disabled")

    def test_prepare_bulk_import_items_pairs_links_with_images_in_natural_order(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            folder = Path(temp_dir.name)
            (folder / "image_10.jpg").write_text("x", encoding="utf-8")
            (folder / "image_2.png").write_text("x", encoding="utf-8")
            (folder / "image_1.jpeg").write_text("x", encoding="utf-8")
            (folder / "ignore.txt").write_text("x", encoding="utf-8")

            app = EditorApplication.__new__(EditorApplication)
            app.config = PipelineConfig()

            bulk_items = app._prepare_bulk_import_items(
                "https://example.com/1\nhttps://example.com/2\nhttps://example.com/3\n",
                str(folder),
            )

            self.assertEqual(
                bulk_items,
                [
                    ("https://example.com/1", folder / "image_1.jpeg"),
                    ("https://example.com/2", folder / "image_2.png"),
                    ("https://example.com/3", folder / "image_10.jpg"),
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_prepare_bulk_import_items_requires_matching_counts(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            folder = Path(temp_dir.name)
            (folder / "image_1.png").write_text("x", encoding="utf-8")

            app = EditorApplication.__new__(EditorApplication)
            app.config = PipelineConfig()

            with self.assertRaisesRegex(ValueError, "bằng nhau"):
                app._prepare_bulk_import_items(
                    "https://example.com/1\nhttps://example.com/2\n",
                    str(folder),
                )
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
