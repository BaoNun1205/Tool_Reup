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

from auto_tiktok_editor.app.media_cleanup import MediaCleanupReport
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.ui.app import EditorApplication

TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))


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
        app.config = PipelineConfig(telegram_bot_token="token", allow_local_telegram=True)
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

    def test_runtime_telegram_config_uses_ui_values_and_locks_chat_allowlist(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()
        app.telegram_bot_token_var = FakeVar("bot-token")
        app.telegram_chat_id_var = FakeVar("123456789")

        runtime_config = app._runtime_telegram_config()

        self.assertTrue(runtime_config.allow_local_telegram)
        self.assertEqual(runtime_config.telegram_bot_token, "bot-token")
        self.assertEqual(runtime_config.telegram_delivery_chat_id, "123456789")
        self.assertEqual(runtime_config.telegram_allowed_chat_ids, (123456789,))

    def test_runtime_telegram_config_allows_token_without_chat_id(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()
        app.telegram_bot_token_var = FakeVar("bot-token")
        app.telegram_chat_id_var = FakeVar("")

        runtime_config = app._runtime_telegram_config()

        self.assertTrue(runtime_config.allow_local_telegram)
        self.assertEqual(runtime_config.telegram_bot_token, "bot-token")
        self.assertEqual(runtime_config.telegram_delivery_chat_id, "")
        self.assertEqual(runtime_config.telegram_allowed_chat_ids, ())

    def test_configured_telegram_bot_configs_collects_multiple_row_bots(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()
        app.telegram_bot_token_var = FakeVar("")
        app.telegram_chat_id_var = FakeVar("")
        app.rows = [
            type("Row", (), {"telegram_bot_token_var": FakeVar("token-a"), "telegram_chat_id_var": FakeVar("111")})(),
            type("Row", (), {"telegram_bot_token_var": FakeVar("token-b"), "telegram_chat_id_var": FakeVar("222")})(),
            type("Row", (), {"telegram_bot_token_var": FakeVar("token-a"), "telegram_chat_id_var": FakeVar("333")})(),
        ]

        configs = app._configured_telegram_bot_configs()

        self.assertEqual(set(configs.keys()), {"token-a", "token-b"})
        self.assertEqual(configs["token-a"].telegram_allowed_chat_ids, (111, 333))
        self.assertEqual(configs["token-b"].telegram_allowed_chat_ids, (222,))

    def test_media_cleanup_config_uses_current_output_root_and_updates_default_input_root(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig(
            default_output_root=Path("d:/old_output"),
            telegram_input_root=Path("d:/old_output/_telegram_inputs"),
        )
        app.output_root_var = FakeVar("d:/new_output")
        app.telegram_bot_token_var = FakeVar("bot-token")
        app.telegram_chat_id_var = FakeVar("")

        cleanup_config = app._media_cleanup_config()

        self.assertEqual(cleanup_config.default_output_root, Path("d:/new_output").resolve())
        self.assertEqual(cleanup_config.telegram_input_root, Path("d:/new_output/_telegram_inputs").resolve())

    def test_extract_telegram_chat_id_from_latest_update(self):
        app = EditorApplication.__new__(EditorApplication)

        chat_id = app._extract_chat_id_from_updates(
            [
                {"update_id": 1, "message": {"chat": {"id": 111}}},
                {"update_id": 2, "edited_message": {"chat": {"id": 222}}},
            ]
        )

        self.assertEqual(chat_id, 222)

    def test_fetch_telegram_chat_id_worker_queues_result(self):
        app = EditorApplication.__new__(EditorApplication)
        queued_events = []
        client = mock.Mock()
        client.get_updates.return_value = [{"message": {"chat": {"id": 654321}}}]
        app.telegram_bot_service = None
        app._telegram_client = mock.Mock(return_value=client)
        app._queue_event = lambda event: queued_events.append(event)

        app._fetch_telegram_chat_id_worker()

        self.assertEqual(queued_events[0].event_type, "telegram_chat_id_lookup_result")
        self.assertEqual(queued_events[0].payload["chat_id"], "654321")

    def test_fetch_telegram_chat_id_worker_prefers_running_bot_state(self):
        app = EditorApplication.__new__(EditorApplication)
        queued_events = []
        app.telegram_bot_service = mock.Mock()
        app.telegram_bot_service.latest_chat_id.return_value = 777888999
        app._telegram_client = mock.Mock()
        app._queue_event = lambda event: queued_events.append(event)

        app._fetch_telegram_chat_id_worker()

        app._telegram_client.assert_not_called()
        self.assertEqual(queued_events[0].event_type, "telegram_chat_id_lookup_result")
        self.assertEqual(queued_events[0].payload["chat_id"], "777888999")

    def test_test_telegram_bot_worker_queues_result(self):
        app = EditorApplication.__new__(EditorApplication)
        queued_events = []
        client = mock.Mock()
        app._telegram_client = mock.Mock(return_value=client)
        app._queue_event = lambda event: queued_events.append(event)

        app._test_telegram_bot_worker(123456)

        client.send_message.assert_called_once()
        self.assertEqual(queued_events[0].event_type, "telegram_test_result")
        self.assertEqual(queued_events[0].payload["chat_id"], "123456")

    def test_cleanup_media_storage_stops_when_telegram_bot_is_processing(self):
        app = EditorApplication.__new__(EditorApplication)
        app.running = False
        app.telegram_bot_service = mock.Mock()
        app.telegram_bot_service.has_processing_jobs.return_value = True

        with mock.patch("auto_tiktok_editor.ui.app.messagebox.showwarning") as showwarning:
            app._cleanup_media_storage()

        showwarning.assert_called_once()

    def test_apply_media_cleanup_result_clears_review_state_when_current_session_is_affected(self):
        app = EditorApplication.__new__(EditorApplication)
        app.rows = [
            type(
                "Row",
                (),
                {
                    "output_dir": "d:/cleanup_root/session_001",
                    "preview_video_path": "d:/cleanup_root/session_001/output.mp4",
                    "open_button": FakeButton(),
                    "rerun_button": FakeButton(),
                },
            )()
        ]
        app.latest_result = mock.Mock(
            artifacts=mock.Mock(session_dir=Path("d:/cleanup_root/session_001")),
            items=[
                mock.Mock(
                    output_dir=Path("d:/cleanup_root/session_001"),
                    artifacts=mock.Mock(final_video_path=Path("d:/cleanup_root/session_001/output.mp4")),
                )
            ],
        )
        app.review_ready = True
        app.current_session_dir = "d:/cleanup_root/session_001"
        app.summary_path_var = FakeVar("old")
        app.summary_counts_var = FakeVar("old counts")
        app.open_session_button = FakeButton()
        app._append_log = lambda *args, **kwargs: None
        app._set_review_action_buttons_state = lambda: None
        app._set_session_status = lambda *args, **kwargs: None
        row_status_updates = []
        app._set_row_status = lambda row, status, detail: row_status_updates.append((status, detail))

        app._apply_media_cleanup_result(
            MediaCleanupReport(
                roots=[Path("d:/cleanup_root")],
                deleted_files=3,
            )
        )

        self.assertIsNone(app.latest_result)
        self.assertFalse(app.review_ready)
        self.assertIsNone(app.current_session_dir)
        self.assertEqual(app.summary_path_var.get(), "Đã dọn video và ảnh trong input/output.")
        self.assertEqual(app.summary_counts_var.get(), "1 item | đã dọn 3 file media")
        self.assertEqual(app.open_session_button.state, "disabled")
        self.assertEqual(app.rows[0].open_button.state, "disabled")
        self.assertEqual(app.rows[0].rerun_button.state, "disabled")
        self.assertTrue(any(status == "draft" for status, _ in row_status_updates))

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
        app.telegram_bot_token_var = FakeVar("")
        app.telegram_chat_id_var = FakeVar("")
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

    def test_build_session_spec_preserves_row_telegram_delivery_settings(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()
        app.output_root_var = FakeVar("d:/output")
        app.session_name_var = FakeVar("demo")
        app.telegram_bot_token_var = FakeVar("")
        app.telegram_chat_id_var = FakeVar("")

        row = type(
            "Row",
            (),
            {
                "row_id": "row_001",
                "url_var": FakeVar("https://www.tiktok.com/@store/video/1234567890"),
                "image_var": FakeVar(str(Path("d:/Tool_Reup/code/tests/fixtures/product.png"))),
                "opacity_var": FakeVar(55),
                "telegram_bot_token_var": FakeVar("bot-token-a"),
                "telegram_chat_id_var": FakeVar("111222333"),
            },
        )()
        app.rows = [row]

        session_spec = app._build_session_spec()

        self.assertEqual(session_spec.items[0].telegram_bot_token, "bot-token-a")
        self.assertEqual(session_spec.items[0].telegram_chat_id, "111222333")

    def test_prepare_bulk_import_items_pairs_links_with_images_in_natural_order(self):
        temp_dir = _temporary_directory()
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
        temp_dir = _temporary_directory()
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
