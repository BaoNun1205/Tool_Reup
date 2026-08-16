import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import json
import tempfile
import unittest
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.error import URLError

from auto_tiktok_editor.app.media_cleanup import cleanup_media_storage, cleanup_tool_storage
from auto_tiktok_editor.app.telegram_bot import (
    TelegramBotService,
    TelegramConversationState,
    TelegramJobResult,
    extract_tiktok_product_id,
    extract_urls_from_caption,
    parse_natural_telegram_caption,
)
from auto_tiktok_editor.config import PipelineConfig

TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))


def _future_schedule_text():
    value = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    minute = (value.minute // 5) * 5
    return value.replace(minute=minute).isoformat(sep=" ", timespec="minutes")


def _select_option(service, chat_id: int, option: int):
    service.handle_update(
        {
            "callback_query": {
                "id": "callback-%s-%s" % (chat_id, option),
                "message": {"chat": {"id": chat_id}},
                "data": "add_video_option:%s" % option,
            }
        }
    )


def _confirm_draft(service, chat_id: int):
    service.handle_update(
        {
            "callback_query": {
                "id": "confirm-%s" % chat_id,
                "message": {"chat": {"id": chat_id}},
                "data": "draft_confirm",
            }
        }
    )


def _photo_caption_update(chat_id: int, file_id: str, caption: str, update_id: int = 1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "photo": [{"file_id": "%s-small" % file_id}, {"file_id": file_id}],
            "caption": caption,
        },
    }


class FakeTelegramClient(object):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sent_messages = []
        self.sent_documents = []

    def get_updates(self, offset, timeout_seconds):
        return []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text))

    def answer_callback_query(self, callback_query_id, text=""):
        return None

    def send_document(self, chat_id, document_path, caption=None, filename=None):
        self.sent_documents.append((chat_id, Path(document_path), caption, filename))

    def download_file(self, file_id, destination_dir, preferred_name=None):
        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = preferred_name or ("%s.jpg" % file_id)
        path = destination_dir / filename
        path.write_text("image", encoding="utf-8")
        return path


class FailingDownloadTelegramClient(FakeTelegramClient):
    def download_file(self, file_id, destination_dir, preferred_name=None):
        raise RuntimeError("download reset")


class FailingSendDocumentTelegramClient(FakeTelegramClient):
    def send_document(self, chat_id, document_path, caption=None, filename=None):
        raise RuntimeError("upload failed")


class FakeUrlopenResponse(object):
    def __init__(self, payload, final_url=None):
        self.payload = payload
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def geturl(self):
        return self.final_url or ""


class FakeTelegramJobRunner(object):
    def __init__(self, video_path: Path):
        self.video_path = video_path
        self.calls = []

    def run(self, chat_id, source_video_url, product_image_path, video_cut_mode=None):
        self.calls.append((chat_id, source_video_url, Path(product_image_path), video_cut_mode))
        return TelegramJobResult(
            ok=True,
            final_video_path=self.video_path,
            source_title="Demo source title",
            session_id="session_demo",
        )


class FlakyTelegramJobRunner(object):
    def __init__(self, video_path: Path, failures_before_success: int, error: str = "Unable to download TikTok source video."):
        self.video_path = video_path
        self.failures_before_success = failures_before_success
        self.error = error
        self.calls = []

    def run(self, chat_id, source_video_url, product_image_path, video_cut_mode=None):
        self.calls.append((chat_id, source_video_url, Path(product_image_path), video_cut_mode))
        if len(self.calls) <= self.failures_before_success:
            return TelegramJobResult(ok=False, error=self.error)
        return TelegramJobResult(
            ok=True,
            final_video_path=self.video_path,
            source_title="Recovered title",
            session_id="session_demo",
        )


class InlineExecutor(object):
    class _InlineFuture(object):
        def __init__(self, result=None, error=None):
            self._result = result
            self._error = error

        def result(self):
            if self._error is not None:
                raise self._error
            return self._result

    def submit(self, fn, *args, **kwargs):
        try:
            return self._InlineFuture(result=fn(*args, **kwargs))
        except Exception as exc:
            return self._InlineFuture(error=exc)


class DeferredExecutor(object):
    class _QueuedFuture(object):
        def result(self):
            return None

    def __init__(self):
        self.pending = []

    def submit(self, fn, *args, **kwargs):
        self.pending.append((fn, args, kwargs))
        return self._QueuedFuture()

    def run_next(self):
        fn, args, kwargs = self.pending.pop(0)
        return fn(*args, **kwargs)


class TelegramBotServiceTests(unittest.TestCase):
    def test_natural_caption_parser_uses_links_in_order_and_time(self):
        data = parse_natural_telegram_caption(
            "Video đây: vt.tiktok.com/abc123\n"
            "Sản phẩm: shopee.vn/product/xyz\n"
            "Đăng lúc 22h30"
        )

        self.assertEqual(data["video_link"], "https://vt.tiktok.com/abc123")
        self.assertEqual(data["product_link"], "https://shopee.vn/product/xyz")
        self.assertIsNotNone(data["schedule_time"])
        self.assertEqual(data["option"], 1)

    def test_natural_caption_parser_warns_when_more_than_two_links(self):
        caption = "https://vt.tiktok.com/a https://shopee.vn/b https://example.com/c"
        self.assertEqual(len(extract_urls_from_caption(caption)), 3)
        data = parse_natural_telegram_caption(caption)

        self.assertEqual(data["video_link"], "https://vt.tiktok.com/a")
        self.assertEqual(data["product_link"], "https://shopee.vn/b")
        self.assertIn("Phát hiện nhiều hơn 2 link", data["warnings"][0])

    def test_extract_tiktok_product_id_from_product_urls(self):
        self.assertEqual(
            extract_tiktok_product_id("https://www.tiktok.com/view/product/1730667245645826792?_svg=1"),
            "1730667245645826792",
        )
        self.assertEqual(
            extract_tiktok_product_id(
                "https://shop.tiktok.com/vn/pdp/banh-trang-nuong-an-lien/1734622779531953483"
            ),
            "1734622779531953483",
        )
        self.assertEqual(
            extract_tiktok_product_id(
                "https://www.tiktok.com/shop/pdp/banh-trang-nuong-an-lien-1734622779531953483.html"
            ),
            "1734622779531953483",
        )
        self.assertEqual(
            extract_tiktok_product_id("https://www.tiktok.com/shop/product?id=1734622779531953483"),
            "1734622779531953483",
        )
        self.assertIsNone(
            extract_tiktok_product_id("https://www.tiktok.com/@store/video/1734622779531953483")
        )

    def test_classify_tiktok_urls_resolves_short_product_link(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            service = TelegramBotService(
                config=PipelineConfig(
                    allow_local_telegram=True,
                    telegram_bot_token="token",
                    telegram_input_root=base_dir / "telegram_inputs",
                ),
                client=FakeTelegramClient(base_dir),
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )
            final_url = "https://www.tiktok.com/view/product/1730667245645826792?_svg=1"
            with mock.patch(
                "auto_tiktok_editor.app.telegram_bot.urlopen",
                return_value=FakeUrlopenResponse({}, final_url=final_url),
            ):
                video_url, product_url, product_id = service._classify_tiktok_urls(
                    "video https://www.tiktok.com/@store/video/1234567890 product https://vt.tiktok.com/ZS9YQhVaQgVMK-PCfnt/"
                )

            self.assertEqual(video_url, "https://www.tiktok.com/@store/video/1234567890")
            self.assertEqual(product_url, final_url)
            self.assertEqual(product_id, "1730667245645826792")
        finally:
            temp_dir.cleanup()

    def test_extract_product_id_after_edit_retries_short_product_redirect(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            service = TelegramBotService(
                config=PipelineConfig(
                    allow_local_telegram=True,
                    telegram_bot_token="token",
                    telegram_input_root=base_dir / "telegram_inputs",
                ),
                client=FakeTelegramClient(base_dir),
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )
            short_url = "https://vt.tiktok.com/ZS9YQhVaQgVMK-PCfnt/"
            error_url = "https://www.tiktok.com/link-error"
            final_url = "https://www.tiktok.com/view/product/1730667245645826792?_svg=1"
            responses = [FakeUrlopenResponse({}, final_url=error_url) for _ in range(4)]
            responses.append(FakeUrlopenResponse({}, final_url=final_url))
            with mock.patch("auto_tiktok_editor.app.telegram_bot.urlopen", side_effect=responses) as mocked_urlopen:
                resolved_url, product_id = service._extract_product_id_after_edit(short_url)

            self.assertEqual(resolved_url, final_url)
            self.assertEqual(product_id, "1730667245645826792")
            self.assertEqual(mocked_urlopen.call_count, 5)
        finally:
            temp_dir.cleanup()

    def test_get_updates_returns_empty_list_on_transient_polling_error(self):
        from auto_tiktok_editor.app.telegram_bot import TelegramBotClient

        real_client = TelegramBotClient("token")
        with mock.patch("auto_tiktok_editor.app.telegram_bot.urlopen", side_effect=URLError("reset")):
            updates = real_client.get_updates(offset=None, timeout_seconds=1)

        self.assertEqual(updates, [])

    def test_send_document_uploads_to_telegram_once(self):
        from auto_tiktok_editor.app.telegram_bot import TelegramBotClient

        real_client = TelegramBotClient("token")

        with mock.patch(
            "auto_tiktok_editor.app.telegram_bot.urlopen",
            return_value=FakeUrlopenResponse({"ok": True, "result": {"message_id": 1}}),
        ) as mocked_urlopen:
            real_client.send_document(123, Path(__file__).resolve(), caption="Demo", filename="video_final.mp4")

        self.assertEqual(mocked_urlopen.call_count, 1)

    def test_poll_offset_is_persisted_per_bot(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
            )
            service = TelegramBotService(
                config=config,
                client=FakeTelegramClient(base_dir),
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )

            service._save_poll_offset(12346)
            reloaded = TelegramBotService(
                config=config,
                client=FakeTelegramClient(base_dir),
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )

            self.assertEqual(reloaded._load_poll_offset(), 12346)
        finally:
            temp_dir.cleanup()

    def test_myid_command_returns_chat_identity(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )

            service.handle_update(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 123456789, "type": "private", "username": "demo_user"},
                        "text": "/myid",
                    },
                }
            )

            self.assertEqual(len(client.sent_messages), 1)
            self.assertIn("Chat ID: 123456789", client.sent_messages[0][1])
            self.assertIn("Loại chat: private", client.sent_messages[0][1])
            self.assertIn("Username: @demo_user", client.sent_messages[0][1])
        finally:
            temp_dir.cleanup()

    def test_collects_link_and_image_then_replies_with_video_and_title(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(video_path)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                telegram_send_result_to_telegram=True,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-large",
                    "\n".join(
                        [
                            "Video đây https://www.tiktok.com/@store/video/1234567890",
                            "Sản phẩm https://www.tiktok.com/view/product/1730667245645826792",
                            _future_schedule_text(),
                        ]
                    ),
                )
            )
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][0], 123)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/1234567890")
            self.assertTrue(job_runner.calls[0][2].exists())
            self.assertEqual(len(client.sent_documents), 1)
            self.assertEqual(client.sent_documents[0][0], 123)
            self.assertEqual(client.sent_documents[0][1], video_path)
            self.assertEqual(client.sent_documents[0][2], "Demo source title")
            self.assertEqual(client.sent_documents[0][3], "video_final.mp4")
            self.assertTrue(any("Đã nhận dữ liệu" in text and "Option: Full" in text for _, text in client.sent_messages))
            return
            self.assertTrue(any("Tóm tắt dữ liệu" in text or "TÃ³m táº¯t dá»¯ liá»‡u" in text for _, text in client.sent_messages))
            self.assertFalse(any(text == "Title: Demo source title" for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_profile_queue_runs_before_telegram_upload_reply(self):
        base_dir = TEST_TEMP_ROOT / "telegram_upload_failure_test"
        base_dir.mkdir(parents=True, exist_ok=True)
        video_path = Path(__file__).resolve()
        client = FailingSendDocumentTelegramClient(base_dir)
        job_runner = FakeTelegramJobRunner(video_path)
        config = PipelineConfig(
            allow_local_telegram=True,
            telegram_bot_token="token",
            telegram_input_root=base_dir / "telegram_inputs",
            default_output_root=base_dir / "output",
            tiktok_profile_slug="demo_profile",
            telegram_send_result_to_telegram=True,
            telegram_cleanup_after_job_enabled=False,
        )
        service = TelegramBotService(
            config=config,
            client=client,
            job_runner=job_runner,
            executor=InlineExecutor(),
        )

        with mock.patch("auto_tiktok_editor.app.telegram_bot.enqueue_telegram_video", return_value=True) as enqueue_mock:
            service._run_job_and_reply(123, "https://www.tiktok.com/@store/video/1234567890", base_dir / "product.jpg")

        enqueue_mock.assert_called_once()
        self.assertEqual(client.sent_documents, [])
        self.assertTrue(any("Da luu video" in text for _, text in client.sent_messages))
        self.assertTrue(any("upload failed" in text for _, text in client.sent_messages))

    def test_save_to_profile_without_send_result_renders_profile_video(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            product_image = base_dir / "product.jpg"
            product_image.write_text("image", encoding="utf-8")
            final_video = base_dir / "final_video.mp4"
            final_video.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(final_video)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                tiktok_profile_slug="demo_profile",
                telegram_send_result_to_telegram=False,
                telegram_save_received_video_to_profile=True,
                telegram_cleanup_after_job_enabled=False,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            draft_video = SimpleNamespace(id=44, account_id=7, cut_mode="scene", note="Telegram source")
            updated_video = SimpleNamespace(id=44, account_id=7)
            manager = mock.Mock()
            manager.mark_video_rendered.return_value = updated_video
            queued_final = base_dir / "queued_final.mp4"
            with mock.patch("auto_tiktok_editor.app.telegram_bot.enqueue_telegram_video_draft", return_value=draft_video) as draft_mock:
                with mock.patch("auto_tiktok_editor.app.telegram_bot.enqueue_telegram_video") as completed_mock:
                    with mock.patch("auto_tiktok_editor.app.telegram_bot.TikTokProfileManager", return_value=manager):
                        with mock.patch(
                            "auto_tiktok_editor.app.telegram_bot.copy_rendered_video_to_queue",
                            return_value=queued_final,
                        ) as copy_mock:
                            service._run_job_and_reply(
                                123,
                                "https://www.tiktok.com/@store/video/1234567890",
                                "https://www.tiktok.com/view/product/1730667245645826792",
                                "1730667245645826792",
                                product_image,
                            )

            draft_mock.assert_called_once()
            completed_mock.assert_not_called()
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][3], "scene")
            manager.update_video_status.assert_any_call(44, "queued", note="Telegram source")
            manager.update_video_status.assert_any_call(44, "rendering", note="Telegram source")
            manager.mark_video_rendered.assert_called_once_with(44, queued_final, source_title="Demo source title")
            copy_mock.assert_called_once_with("demo_profile", final_video)
            self.assertEqual(client.sent_documents, [])
            self.assertTrue(any("bat dau tao video" in text for _, text in client.sent_messages))
            self.assertTrue(any("Da tao xong video" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_save_to_profile_creates_queued_profile_video_before_job_starts(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            product_image = base_dir / "product.jpg"
            product_image.write_text("image", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                tiktok_profile_slug="demo_profile",
                telegram_send_result_to_telegram=False,
                telegram_save_received_video_to_profile=True,
                telegram_cleanup_after_job_enabled=False,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )

            draft_video = SimpleNamespace(id=55, account_id=7, cut_mode="original", note="Telegram source")
            manager = mock.Mock()
            with mock.patch("auto_tiktok_editor.app.telegram_bot.enqueue_telegram_video_draft", return_value=draft_video) as draft_mock:
                with mock.patch("auto_tiktok_editor.app.telegram_bot.TikTokProfileManager", return_value=manager):
                    service.save_telegram_video_input(
                        123,
                        {
                            "option": 2,
                            "video_link": "https://www.tiktok.com/@store/video/1234567890",
                            "product_link": "https://www.tiktok.com/view/product/1730667245645826792",
                            "image": product_image,
                        },
                    )

            draft_mock.assert_called_once()
            manager.update_video_status.assert_called_once_with(55, "queued", note="Telegram source")
            queued_jobs = service._chat_states[123].queued_jobs
            self.assertEqual(len(queued_jobs), 1)
            self.assertEqual(queued_jobs[0].profile_video_id, 55)
            self.assertEqual(queued_jobs[0].profile_video_cut_mode, "original")
            self.assertEqual(service.job_runner.calls, [])
        finally:
            temp_dir.cleanup()

    def test_send_result_to_telegram_sends_product_id_after_video(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(video_path)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                telegram_send_result_to_telegram=True,
                telegram_save_received_video_to_profile=False,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            service._run_job_and_reply(
                123,
                "https://www.tiktok.com/@store/video/1234567890",
                "https://www.tiktok.com/view/product/1730667245645826792",
                "1730667245645826792",
                base_dir / "product.jpg",
            )

            self.assertEqual(len(client.sent_documents), 1)
            self.assertEqual(client.sent_documents[0][0], 123)
            self.assertIn((123, "1730667245645826792"), client.sent_messages)
        finally:
            temp_dir.cleanup()

    def test_send_result_to_telegram_uses_mapped_profile_cut_mode(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(video_path)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                tiktok_profile_slug="tep_riu",
                video_cut_mode="original",
                telegram_send_result_to_telegram=True,
                telegram_save_received_video_to_profile=False,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )
            manager = mock.Mock()
            manager.find_account_for_profile_slug.return_value = SimpleNamespace(name="Tep Riu", cut_mode="scene")

            with mock.patch("auto_tiktok_editor.app.telegram_bot.TikTokProfileManager", return_value=manager):
                service._run_job_and_reply(
                    123,
                    "https://www.tiktok.com/@store/video/1234567890",
                    "https://www.tiktok.com/view/product/1730667245645826792",
                    "1730667245645826792",
                    base_dir / "product.jpg",
                )

            self.assertEqual(job_runner.calls[0][3], "scene")
            self.assertEqual(len(client.sent_documents), 1)
        finally:
            temp_dir.cleanup()

    def test_simple_input_mode_only_requires_video_link_and_image(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(video_path)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            with mock.patch.dict(os.environ, {"AUTO_EDITOR_TELEGRAM_INPUT_MODE": "simple"}):
                service = TelegramBotService(
                    config=config,
                    client=client,
                    job_runner=job_runner,
                    executor=InlineExecutor(),
                )

            service.handle_update(
                _photo_caption_update(123, "photo-large", "https://www.tiktok.com/@store/video/1234567890")
            )

            self.assertEqual(len(job_runner.calls), 1)
            self.assertFalse(any("Gửi link sản phẩm" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_full_input_mode_requires_product_link_and_time(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            with mock.patch.dict(os.environ, {"AUTO_EDITOR_TELEGRAM_INPUT_MODE": "full"}):
                service = TelegramBotService(
                    config=config,
                    client=client,
                    job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                    executor=InlineExecutor(),
                )

            service.handle_update(_photo_caption_update(123, "photo-large", "p: https://shop.example/item"))
            self.assertEqual(service.job_runner.calls, [])
            self.assertTrue(any("Thiếu link video" in text for _, text in client.sent_messages))
            return
            _select_option(service, 123, 1)
            service.handle_update({"message": {"chat": {"id": 123}, "photo": [{"file_id": "photo-large"}]}})
            service.handle_update({"message": {"chat": {"id": 123}, "text": "https://www.tiktok.com/@store/video/1234567890"}})
            self.assertTrue(any("Gửi link sản phẩm" in text or "Gá»­i link sáº£n pháº©m" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_option_2_does_not_require_schedule_time(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(video_path),
                executor=InlineExecutor(),
            )

            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-large",
                    "\n".join(
                        [
                            "https://www.tiktok.com/@store/video/1234567890",
                            "https://www.tiktok.com/view/product/1730667245645826792",
                        ]
                    ),
                )
            )
            self.assertEqual(len(service.job_runner.calls), 1)
            self.assertTrue(any("Option: No schedule" in text for _, text in client.sent_messages))
            return
            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-a",
                    "\n".join(
                        [
                            "https://www.tiktok.com/@store/video/111",
                            "https://www.tiktok.com/view/product/1730667245645826792",
                        ]
                    ),
                    update_id=1,
                )
            )
            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-b",
                    "\n".join(
                        [
                            "https://www.tiktok.com/@store/video/222",
                            "https://www.tiktok.com/view/product/1730667245645826792",
                        ]
                    ),
                    update_id=2,
                )
            )
            self.assertEqual(len(executor.pending), 1)
            self.assertEqual(job_runner.calls, [])

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/111")
            self.assertEqual(len(executor.pending), 1)

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 2)
            self.assertEqual(job_runner.calls[1][1], "https://www.tiktok.com/@store/video/222")
            self.assertEqual(len(client.sent_documents), 2)
            self.assertTrue(all(item[3] == "video_final.mp4" for item in client.sent_documents))
            return
            _select_option(service, 123, 2)
            service.handle_update({"message": {"chat": {"id": 123}, "photo": [{"file_id": "photo-large"}]}})
            service.handle_update({"message": {"chat": {"id": 123}, "text": "https://www.tiktok.com/@store/video/1234567890"}})
            service.handle_update({"message": {"chat": {"id": 123}, "text": "https://www.tiktok.com/view/product/1730667245645826792"}})
            _confirm_draft(service, 123)

            self.assertEqual(len(service.job_runner.calls), 1)
        finally:
            temp_dir.cleanup()

    def test_image_download_failure_is_reported_without_crashing_polling(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FailingDownloadTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(base_dir / "unused.mp4")
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            _select_option(service, 123, 3)
            service.handle_update(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-large"}],
                        "caption": "v: https://www.tiktok.com/@store/video/1234567890",
                    },
                }
            )

            self.assertEqual(job_runner.calls, [])
            self.assertTrue(any("tải được ảnh" in text or "táº£i Ä‘Æ°á»£c áº£nh" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_retries_failed_job_before_replying_with_video(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FlakyTelegramJobRunner(video_path, failures_before_success=2)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                telegram_send_result_to_telegram=True,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            service._run_job_and_reply(123, "https://www.tiktok.com/@store/video/retry", base_dir / "product.jpg")

            self.assertEqual(len(job_runner.calls), 3)
            self.assertEqual(len(client.sent_documents), 1)
            self.assertTrue(any("Job loi o lan 1/3" in text for _, text in client.sent_messages))
            self.assertTrue(any("Job loi o lan 2/3" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_reports_error_after_three_failed_job_attempts(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            job_runner = FlakyTelegramJobRunner(base_dir / "missing.mp4", failures_before_success=3)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            service._run_job_and_reply(123, "https://www.tiktok.com/@store/video/fail", base_dir / "product.jpg")

            self.assertEqual(len(job_runner.calls), 3)
            self.assertEqual(client.sent_documents, [])
            self.assertTrue(any("Job bi loi" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_does_not_retry_non_download_processing_error(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            job_runner = FlakyTelegramJobRunner(
                base_dir / "missing.mp4",
                failures_before_success=3,
                error="render compositor failed",
            )
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=InlineExecutor(),
            )

            service._run_job_and_reply(123, "https://www.tiktok.com/@store/video/fail", base_dir / "product.jpg")

            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(client.sent_documents, [])
            self.assertFalse(any("bot se tu lam lai" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_rejects_chat_when_allowlist_is_configured(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                telegram_allowed_chat_ids=(999,),
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )

            service.handle_update(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/@store/video/1234567890",
                    },
                }
            )

            self.assertEqual(client.sent_messages, [(123, "Chat này chưa được cấp quyền dùng bot.")])
            self.assertEqual(client.sent_documents, [])
        finally:
            temp_dir.cleanup()

    def test_queues_multiple_jobs_for_the_same_chat_and_processes_them_in_order(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_path = base_dir / "final_video.mp4"
            video_path.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            job_runner = FakeTelegramJobRunner(video_path)
            executor = DeferredExecutor()
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
                telegram_send_result_to_telegram=True,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=executor,
            )

            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-a",
                    "\n".join(
                        [
                            "v: https://www.tiktok.com/@store/video/111",
                            "p: https://www.tiktok.com/view/product/1730667245645826792",
                        ]
                    ),
                    update_id=1,
                )
            )
            service.handle_update(
                _photo_caption_update(
                    123,
                    "photo-b",
                    "\n".join(
                        [
                            "v: https://www.tiktok.com/@store/video/222",
                            "p: https://www.tiktok.com/view/product/1730667245645826792",
                        ]
                    ),
                    update_id=2,
                )
            )
            self.assertEqual(len(executor.pending), 1)
            self.assertEqual(job_runner.calls, [])

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/111")
            self.assertEqual(len(executor.pending), 1)

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 2)
            self.assertEqual(job_runner.calls[1][1], "https://www.tiktok.com/@store/video/222")
            self.assertEqual(len(client.sent_documents), 2)
            self.assertTrue(all(item[3] == "video_final.mp4" for item in client.sent_documents))
            return
            _select_option(service, 123, 2)
            service.handle_update(
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-a"}],
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/@store/video/111",
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 3,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/view/product/1730667245645826792",
                    },
                }
            )
            _confirm_draft(service, 123)
            _select_option(service, 123, 2)
            service.handle_update(
                {
                    "update_id": 6,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-b"}],
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 5,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/@store/video/222",
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 7,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/view/product/1730667245645826792",
                    },
                }
            )
            _confirm_draft(service, 123)

            self.assertEqual(len(executor.pending), 1)
            self.assertEqual(job_runner.calls, [])
            self.assertTrue(any("hàng đợi" in text or "hÃ ng Ä‘á»£i" in text for _, text in client.sent_messages))

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/111")
            self.assertEqual(len(executor.pending), 1)

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 2)
            self.assertEqual(job_runner.calls[1][1], "https://www.tiktok.com/@store/video/222")
            self.assertEqual(len(client.sent_documents), 2)
            self.assertTrue(all(item[3] == "video_final.mp4" for item in client.sent_documents))
        finally:
            temp_dir.cleanup()

    def test_cleanup_command_deletes_media_and_clears_pending_jobs(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            telegram_input_root = base_dir / "telegram_inputs"
            output_root = base_dir / "output"
            input_image = telegram_input_root / "chat_123" / "product.jpg"
            output_video = output_root / "session_001" / "output.mp4"
            input_image.parent.mkdir(parents=True, exist_ok=True)
            output_video.parent.mkdir(parents=True, exist_ok=True)
            input_image.write_text("image", encoding="utf-8")
            output_video.write_text("video", encoding="utf-8")
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=telegram_input_root,
                default_output_root=output_root,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )
            service._chat_states[123] = TelegramConversationState(
                draft_source_video_url="https://www.tiktok.com/@store/video/cleanup",
            )

            service.handle_update({"message": {"chat": {"id": 123}, "text": "/cleanup"}})

            self.assertFalse(input_image.exists())
            self.assertFalse(output_video.exists())
            self.assertIsNone(service._chat_states[123].draft_source_video_url)
            self.assertEqual(service._chat_states[123].queued_jobs, [])
            self.assertTrue(any("Hang doi Telegram" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_cleanup_command_refuses_while_processing_job_is_running(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=base_dir / "output",
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )
            service._chat_states[123] = TelegramConversationState(processing=True)

            service.handle_update({"message": {"chat": {"id": 123}, "text": "/cleanup"}})

            self.assertTrue(any("Dang co job Telegram" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_cleanup_media_storage_can_keep_recent_files(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            output_root = base_dir / "output"
            old_video = output_root / "session_old" / "old.mp4"
            recent_video = output_root / "session_recent" / "recent.mp4"
            old_video.parent.mkdir(parents=True, exist_ok=True)
            recent_video.parent.mkdir(parents=True, exist_ok=True)
            old_video.write_text("old", encoding="utf-8")
            recent_video.write_text("recent", encoding="utf-8")
            old_timestamp = time.time() - 7200
            os.utime(old_video, (old_timestamp, old_timestamp))
            config = PipelineConfig(
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=output_root,
            )

            report = cleanup_media_storage(config, older_than_seconds=3600)

            self.assertEqual(report.deleted_files, 1)
            self.assertFalse(old_video.exists())
            self.assertTrue(recent_video.exists())
        finally:
            temp_dir.cleanup()

    def test_cleanup_media_storage_keeps_recent_empty_session_directories(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            output_root = base_dir / "output"
            recent_session_dir = output_root / "session_running" / "items" / "item_001" / "clips"
            old_session_dir = output_root / "session_old_empty" / "items"
            recent_session_dir.mkdir(parents=True, exist_ok=True)
            old_session_dir.mkdir(parents=True, exist_ok=True)
            old_timestamp = time.time() - 7200
            for path in (old_session_dir, old_session_dir.parent):
                os.utime(path, (old_timestamp, old_timestamp))
            config = PipelineConfig(
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=output_root,
            )

            report = cleanup_media_storage(config, older_than_seconds=3600)

            self.assertGreaterEqual(report.deleted_directories, 1)
            self.assertTrue(recent_session_dir.exists())
            self.assertFalse(old_session_dir.exists())
        finally:
            temp_dir.cleanup()

    def test_cleanup_tool_storage_removes_runtime_data_but_keeps_profiles_database_and_config(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            output_root = base_dir / "output"
            telegram_input_root = output_root / "_telegram_inputs"
            paths_to_delete = [
                output_root / "session_001" / "final_video.mp4",
                telegram_input_root / "chat_123" / "product.jpg",
                base_dir / "profile_video_queue" / "profile" / "queued.mp4",
                base_dir / "tmp" / "temp.bin",
                base_dir / "phone_screenshots" / "screen.png",
                base_dir / "logs" / "telegram_bot_stdout.log",
            ]
            for path in paths_to_delete:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("data", encoding="utf-8")
            db_path = base_dir / "tiktok_profile_manager.sqlite3"
            bot_config_path = base_dir / "telegram_bots.json"
            profile_cache_path = base_dir / "profiles" / "profile" / "Default" / "Cache" / "blob"
            db_path.write_text("db", encoding="utf-8")
            bot_config_path.write_text("{}", encoding="utf-8")
            profile_cache_path.parent.mkdir(parents=True, exist_ok=True)
            profile_cache_path.write_text("profile", encoding="utf-8")

            report = cleanup_tool_storage(
                PipelineConfig(
                    default_output_root=output_root,
                    telegram_input_root=telegram_input_root,
                ),
                base_dir,
            )

            self.assertGreaterEqual(report.deleted_files, len(paths_to_delete))
            for path in paths_to_delete:
                self.assertFalse(path.exists(), path)
            self.assertTrue(db_path.exists())
            self.assertTrue(bot_config_path.exists())
            self.assertTrue(profile_cache_path.exists())
        finally:
            temp_dir.cleanup()

    def test_auto_cleanup_skips_when_processing_job_is_running(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            output_root = base_dir / "output"
            old_video = output_root / "session_old" / "old.mp4"
            old_video.parent.mkdir(parents=True, exist_ok=True)
            old_video.write_text("old", encoding="utf-8")
            old_timestamp = time.time() - 7200
            os.utime(old_video, (old_timestamp, old_timestamp))
            client = FakeTelegramClient(base_dir)
            config = PipelineConfig(
                allow_local_telegram=True,
                telegram_bot_token="token",
                telegram_input_root=base_dir / "telegram_inputs",
                default_output_root=output_root,
                telegram_cleanup_interval_seconds=60,
                telegram_cleanup_max_age_seconds=3600,
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=FakeTelegramJobRunner(base_dir / "unused.mp4"),
                executor=InlineExecutor(),
            )
            service._chat_states[123] = TelegramConversationState(processing=True)

            service._maybe_cleanup_expired_media()

            self.assertTrue(old_video.exists())
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
