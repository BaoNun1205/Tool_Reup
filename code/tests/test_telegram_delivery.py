import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import tempfile
import unittest

from auto_tiktok_editor.app.telegram_delivery import TelegramDeliveryService
from auto_tiktok_editor.domain.models import JobArtifacts, ItemProcessResult, SessionResult

TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))


class FakeTelegramClient(object):
    def __init__(self):
        self.sent_messages = []
        self.sent_documents = []

    def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))

    def send_document(self, chat_id, document_path, caption=None, filename=None):
        self.sent_documents.append((chat_id, Path(document_path), caption, filename))


class TelegramDeliveryServiceTests(unittest.TestCase):
    def test_send_session_result_sends_completed_videos_in_order(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_a = base_dir / "a.mp4"
            video_b = base_dir / "b.mp4"
            video_a.write_text("a", encoding="utf-8")
            video_b.write_text("b", encoding="utf-8")
            client = FakeTelegramClient()
            service = TelegramDeliveryService(client=client)

            result = SessionResult(
                session_id="session_demo",
                status="completed_with_success",
                items=[
                    ItemProcessResult(
                        item_index=1,
                        row_id="row_002",
                        job_id="job_b",
                        status="completed",
                        source_video_url="https://example.com/b",
                        product_image_path=None,
                        output_dir=base_dir,
                        artifacts=JobArtifacts(
                            output_dir=base_dir,
                            final_video_path=video_b,
                            final_audio_path=None,
                            video_title_path=None,
                            metadata_path=None,
                            process_log_path=None,
                        ),
                        metadata={"source_title": "Title B"},
                    ),
                    ItemProcessResult(
                        item_index=0,
                        row_id="row_001",
                        job_id="job_a",
                        status="completed",
                        source_video_url="https://example.com/a",
                        product_image_path=None,
                        output_dir=base_dir,
                        artifacts=JobArtifacts(
                            output_dir=base_dir,
                            final_video_path=video_a,
                            final_audio_path=None,
                            video_title_path=None,
                            metadata_path=None,
                            process_log_path=None,
                        ),
                        metadata={"source_title": "Title A"},
                    ),
                ],
            )

            payload = service.send_session_result(result, 123456)

            self.assertEqual(payload["sent_count"], 2)
            self.assertEqual(client.sent_documents[0], (123456, video_a, "Title A", "video_final.mp4"))
            self.assertEqual(client.sent_documents[1], (123456, video_b, "Title B", "video_final.mp4"))
            self.assertTrue(any("Đã gửi 2 video" in text for _, text in client.sent_messages))
        finally:
            temp_dir.cleanup()

    def test_send_session_result_uses_each_items_bot_and_chat(self):
        temp_dir = _temporary_directory()
        try:
            base_dir = Path(temp_dir.name)
            video_a = base_dir / "a.mp4"
            video_b = base_dir / "b.mp4"
            video_a.write_text("a", encoding="utf-8")
            video_b.write_text("b", encoding="utf-8")
            clients = {}

            def client_factory(token):
                clients[token] = FakeTelegramClient()
                return clients[token]

            service = TelegramDeliveryService(
                config=type("Config", (), {"telegram_bot_token": "", "telegram_delivery_chat_id": ""})(),
                client_factory=client_factory,
            )

            result = SessionResult(
                session_id="session_demo",
                status="completed_with_success",
                items=[
                    ItemProcessResult(
                        item_index=0,
                        row_id="row_001",
                        job_id="job_a",
                        status="completed",
                        source_video_url="https://example.com/a",
                        product_image_path=None,
                        output_dir=base_dir,
                        artifacts=JobArtifacts(
                            output_dir=base_dir,
                            final_video_path=video_a,
                            final_audio_path=None,
                            video_title_path=None,
                            metadata_path=None,
                            process_log_path=None,
                        ),
                        metadata={"source_title": "Title A"},
                        telegram_bot_token="token-a",
                        telegram_chat_id="111",
                    ),
                    ItemProcessResult(
                        item_index=1,
                        row_id="row_002",
                        job_id="job_b",
                        status="completed",
                        source_video_url="https://example.com/b",
                        product_image_path=None,
                        output_dir=base_dir,
                        artifacts=JobArtifacts(
                            output_dir=base_dir,
                            final_video_path=video_b,
                            final_audio_path=None,
                            video_title_path=None,
                            metadata_path=None,
                            process_log_path=None,
                        ),
                        metadata={"source_title": "Title B"},
                        telegram_bot_token="token-b",
                        telegram_chat_id="222",
                    ),
                ],
            )

            payload = service.send_session_result(result)

            self.assertEqual(payload["sent_count"], 2)
            self.assertEqual(payload["chat_ids"], [111, 222])
            self.assertEqual(clients["token-a"].sent_documents[0], (111, video_a, "Title A", "video_final.mp4"))
            self.assertEqual(clients["token-b"].sent_documents[0], (222, video_b, "Title B", "video_final.mp4"))
        finally:
            temp_dir.cleanup()

    def test_send_session_result_requires_completed_video(self):
        client = FakeTelegramClient()
        service = TelegramDeliveryService(client=client)
        result = SessionResult(session_id="session_demo", status="failed_session", items=[])

        with self.assertRaisesRegex(ValueError, "chưa có video hoàn tất"):
            service.send_session_result(result, 123456)


if __name__ == "__main__":
    unittest.main()
