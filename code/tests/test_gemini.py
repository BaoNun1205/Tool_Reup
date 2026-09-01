import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.app.gemini import (
    GeminiRequestError,
    chat_with_gemini,
    describe_fashion_image,
    write_fashion_product_copy,
)
from auto_tiktok_editor.fashion_prompts import (
    FashionPromptPreset,
    FASHION_PROMPT_PRESETS,
    get_fashion_prompt_preset,
    is_change_outfit_prompt_name,
)
from auto_tiktok_editor.fashion_prompt_settings import load_garment_prompts


class _FakeResponse:
    def __init__(self, body: dict):
        self.body = body

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class GeminiImageDescriptionTests(unittest.TestCase):
    def test_sends_image_and_returns_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "look.png"
            image_path.write_bytes(b"image-bytes")
            response = _FakeResponse(
                {"candidates": [{"content": {"parts": [{"text": "editorial fashion video prompt"}]}}]}
            )

            with mock.patch("auto_tiktok_editor.app.gemini.urlopen", return_value=response) as mock_urlopen:
                prompt = describe_fashion_image(image_path, "test-key")

        self.assertEqual(prompt, "editorial fashion video prompt")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        inline_data = payload["contents"][0]["parts"][0]["inline_data"]
        self.assertEqual(inline_data["mime_type"], "image/png")
        self.assertEqual(inline_data["data"], base64.b64encode(b"image-bytes").decode("ascii"))

    def test_requires_api_key(self):
        with self.assertRaisesRegex(GeminiRequestError, "API key"):
            describe_fashion_image("missing.jpg", "")

    def test_chat_returns_gemini_text(self):
        response = _FakeResponse({"candidates": [{"content": {"parts": [{"text": "Gemini is ready."}]}}]})
        with mock.patch("auto_tiktok_editor.app.gemini.urlopen", return_value=response) as mock_urlopen:
            answer = chat_with_gemini("Say hello", "test-key", "gemini-3.6-flash")

        self.assertEqual(answer, "Gemini is ready.")
        request = mock_urlopen.call_args.args[0]
        self.assertIn("gemini-3.6-flash", request.full_url)

    def test_chat_can_send_an_attached_image_without_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.jpg"
            image_path.write_bytes(b"attached-image")
            response = _FakeResponse({"candidates": [{"content": {"parts": [{"text": "Image received."}]}}]})
            with mock.patch("auto_tiktok_editor.app.gemini.urlopen", return_value=response) as mock_urlopen:
                answer = chat_with_gemini("", "test-key", image_path=image_path)

        self.assertEqual(answer, "Image received.")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/jpeg")
        self.assertEqual(parts[1]["text"], "Describe this image in detail.")

    def test_fashion_product_copy_keeps_a_concise_caption_and_five_hashtags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "product.jpg"
            image_path.write_bytes(b"product-image")
            response = _FakeResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "caption": "Áo thun Boxy này mặc lên cực chất.",
                                                "hashtags": ["thoitrang", "aothun", "boxy", "outfit", "tiktokshop"],
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
            with mock.patch("auto_tiktok_editor.app.gemini.urlopen", return_value=response):
                result = write_fashion_product_copy(image_path, "Áo thun Boxy", "test-key")

        self.assertEqual(result.caption, "Áo thun Boxy này mặc lên cực chất.")
        self.assertEqual(len(result.hashtags), 5)
        self.assertEqual(result.hashtags[0], "#thoitrang")

    def test_fashion_caption_instruction_omits_product_name_and_limits_length(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "product.jpg"
            image_path.write_bytes(b"product-image")
            response = _FakeResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "caption": "Phoi do nhanh, mac ca ngay van on.",
                                                "hashtags": ["thoitrang", "outfit", "phoidodep", "tiktokshop", "dailylook"],
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
            with mock.patch("auto_tiktok_editor.app.gemini.urlopen", return_value=response) as mock_urlopen:
                write_fashion_product_copy(image_path, "Boxy Tee", "test-key")

        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        instruction = payload["contents"][0]["parts"][1]["text"]
        self.assertIn("no more than 12 words", instruction)
        self.assertIn("Do NOT mention, repeat, shorten, paraphrase", instruction)
        self.assertNotIn("Boxy Tee", instruction)
        self.assertIn("describe only the specific product", instruction)
        self.assertIn("#xuhuong, #fyp, #viral", instruction)

    def test_t_shirt_preset_contains_both_requested_scenes(self):
        self.assertEqual(len(FASHION_PROMPT_PRESETS), 1)
        preset = get_fashion_prompt_preset("t_shirt")
        self.assertEqual(preset.label, "Áo thun")
        self.assertIn("tay trái", preset.first_scene)
        self.assertIn("30 độ sang phải", preset.second_scene)

    def test_recognizes_change_outfit_prompt_name_with_or_without_accents(self):
        self.assertTrue(is_change_outfit_prompt_name("Thay đồ"))
        self.assertTrue(is_change_outfit_prompt_name("Prompt thay do chung"))
        self.assertFalse(is_change_outfit_prompt_name("Cảnh tạo dáng"))

    def test_new_garment_type_starts_with_an_empty_prompt_list(self):
        new_type = FashionPromptPreset(
            key="test_new_empty_garment_type",
            label="Test new garment type",
            first_scene="",
            second_scene="",
        )
        self.assertEqual(load_garment_prompts(new_type), [])
