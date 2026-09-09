import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.app.gemini import GeminiRequestError, rewrite_facebook_product_name
from auto_tiktok_editor.facebook.queue import prepare_facebook_video, send_facebook_video_to_phone
from auto_tiktok_editor.config import PipelineConfig


class FacebookQueueTests(unittest.TestCase):
    def test_rewrite_product_name_accepts_single_name_within_50_characters(self):
        with mock.patch(
            "auto_tiktok_editor.app.gemini._generate_content",
            return_value='"Áo sơ mi nữ tay dài thanh lịch"',
        ) as generate:
            result = rewrite_facebook_product_name("AO NU 123 SALE", "key", "gemini-2.5-flash")

        self.assertEqual(result, "Áo sơ mi nữ tay dài thanh lịch")
        prompt = generate.call_args.kwargs["parts"][0]["text"]
        self.assertIn("at most 50 characters", prompt)
        self.assertIn("AO NU 123 SALE", prompt)

    def test_rewrite_product_name_rejects_more_than_50_characters(self):
        with mock.patch(
            "auto_tiktok_editor.app.gemini._generate_content",
            return_value="x" * 51,
        ):
            with self.assertRaisesRegex(GeminiRequestError, "50 ký tự"):
                rewrite_facebook_product_name("Tên gốc", "key")

    def test_prepare_facebook_video_resolves_link_and_saves_gemini_name(self):
        queued = SimpleNamespace(id=7, product_url="https://vt.tiktok.com/product/")
        manager = mock.Mock()
        manager.get_facebook_video.return_value = queued
        manager.update_facebook_video_preparation.return_value = "updated"
        with mock.patch(
            "auto_tiktok_editor.facebook.queue.resolve_tiktok_shop_product_title",
            return_value="Tên sản phẩm gốc",
        ), mock.patch(
            "auto_tiktok_editor.facebook.queue.rewrite_facebook_product_name",
            return_value="Tên phù hợp Reels",
        ):
            result = prepare_facebook_video(manager, queued.id)

        self.assertEqual(result, "updated")
        manager.update_facebook_video_status.assert_called_once_with(7, "processing", note="")
        manager.update_facebook_video_preparation.assert_called_once_with(
            7,
            source_product_name="Tên sản phẩm gốc",
            display_product_name="Tên phù hợp Reels",
            status="ready",
            note="",
        )

    def test_send_facebook_video_uses_display_name_instead_of_product_id(self):
        video = SimpleNamespace(
            id=8,
            source_video_id=12,
            source_account_id=3,
            caption="Mô tả",
            hashtags="#sanpham",
            display_product_name="Tên sản phẩm cho Reels",
        )
        manager = mock.Mock()
        manager.get_facebook_video.return_value = video
        manager.resolve_facebook_video_path.return_value = Path("video.mp4")
        controller = mock.Mock()
        controller.send_file_to_gallery.return_value = {"address": "device-1", "remote_path": "/video.mp4"}

        result = send_facebook_video_to_phone(
            manager,
            PipelineConfig(),
            video.id,
            address="device-1",
            connection_mode="usb",
            controller=controller,
        )

        self.assertEqual(result["address"], "device-1")
        self.assertEqual(controller.copy_text_to_clipboard.call_count, 2)
        first_call, second_call = controller.copy_text_to_clipboard.call_args_list
        self.assertEqual(first_call.args[0], "Mô tả #sanpham")
        self.assertEqual(second_call.args[0], "Tên sản phẩm cho Reels")
        self.assertEqual(second_call.kwargs["label"], "Facebook product display name")
        manager.update_facebook_video_status.assert_called_once_with(8, "sent", note="")


if __name__ == "__main__":
    unittest.main()
