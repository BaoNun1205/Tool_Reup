import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import tempfile
import unittest

from auto_tiktok_editor.app.telegram_bot import TelegramBotService, TelegramJobResult
from auto_tiktok_editor.config import PipelineConfig


class FakeTelegramClient(object):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sent_messages = []
        self.sent_videos = []

    def get_updates(self, offset, timeout_seconds):
        return []

    def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))

    def send_video(self, chat_id, video_path, caption=None):
        self.sent_videos.append((chat_id, Path(video_path), caption))

    def download_file(self, file_id, destination_dir, preferred_name=None):
        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = preferred_name or ("%s.jpg" % file_id)
        path = destination_dir / filename
        path.write_text("image", encoding="utf-8")
        return path


class FakeTelegramJobRunner(object):
    def __init__(self, video_path: Path):
        self.video_path = video_path
        self.calls = []

    def run(self, chat_id, source_video_url, product_image_path):
        self.calls.append((chat_id, source_video_url, Path(product_image_path)))
        return TelegramJobResult(
            ok=True,
            final_video_path=self.video_path,
            source_title="Demo source title",
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
    def test_myid_command_returns_chat_identity(self):
        temp_dir = tempfile.TemporaryDirectory()
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
        temp_dir = tempfile.TemporaryDirectory()
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
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
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
            service.handle_update(
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-small"}, {"file_id": "photo-large"}],
                    },
                }
            )

            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][0], 123)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/1234567890")
            self.assertTrue(job_runner.calls[0][2].exists())
            self.assertEqual(len(client.sent_videos), 1)
            self.assertEqual(client.sent_videos[0][0], 123)
            self.assertEqual(client.sent_videos[0][1], video_path)
            self.assertEqual(client.sent_videos[0][2], "Demo source title")
            self.assertTrue(any("Đã nhận link TikTok" in text for _, text in client.sent_messages))
            self.assertTrue(any("Đã nhận đủ link TikTok và ảnh sản phẩm" in text for _, text in client.sent_messages))
            self.assertFalse(any(text == "Title: Demo source title" for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_rejects_chat_when_allowlist_is_configured(self):
        temp_dir = tempfile.TemporaryDirectory()
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
            self.assertEqual(client.sent_videos, [])
        finally:
            temp_dir.cleanup()

    def test_queues_multiple_jobs_for_the_same_chat_and_processes_them_in_order(self):
        temp_dir = tempfile.TemporaryDirectory()
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
            )
            service = TelegramBotService(
                config=config,
                client=client,
                job_runner=job_runner,
                executor=executor,
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
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-a"}],
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 3,
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://www.tiktok.com/@store/video/222",
                    },
                }
            )
            service.handle_update(
                {
                    "update_id": 4,
                    "message": {
                        "chat": {"id": 123},
                        "photo": [{"file_id": "photo-b"}],
                    },
                }
            )

            self.assertEqual(len(executor.pending), 1)
            self.assertEqual(job_runner.calls, [])
            self.assertTrue(any("thêm vào hàng đợi" in text for _, text in client.sent_messages))

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 1)
            self.assertEqual(job_runner.calls[0][1], "https://www.tiktok.com/@store/video/111")
            self.assertEqual(len(executor.pending), 1)

            executor.run_next()
            self.assertEqual(len(job_runner.calls), 2)
            self.assertEqual(job_runner.calls[1][1], "https://www.tiktok.com/@store/video/222")
            self.assertEqual(len(client.sent_videos), 2)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
