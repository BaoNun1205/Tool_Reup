import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.app.tiktok_shop import resolve_tiktok_shop_product
from auto_tiktok_editor.app.fashion_bot import FashionProductBotService
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.fashion_bot_settings import FashionBotSettings
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager


class _Response:
    def __init__(self, url: str, body: bytes = b"", content_type: str = "text/html"):
        self._url = url
        self._body = body
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self._url

    def read(self, _size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FashionProductTests(unittest.TestCase):
    def test_resolves_title_image_and_product_id_from_redirect_og_info(self):
        og_info = json.dumps(
            {"title": "Áo thun Boxy", "image": "https://p16.example/product.webp"},
            ensure_ascii=False,
        )
        final_url = "https://shop.tiktok.com/vn/pdp/1737062736670590410?" + urlencode({"og_info": og_info})
        with mock.patch(
            "auto_tiktok_editor.app.tiktok_shop.urlopen",
            return_value=_Response(final_url),
        ):
            product = resolve_tiktok_shop_product("https://vt.tiktok.com/example")

        self.assertEqual(product.product_id, "1737062736670590410")
        self.assertEqual(product.title, "Áo thun Boxy")
        self.assertEqual(product.image_url, "https://p16.example/product.webp")

    def test_fashion_product_record_persists_generated_description_and_sent_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "fashion_products" / "images" / "product.webp"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"image")
            manager = TikTokProfileManager(
                db_path=root / "manager.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            product = manager.add_fashion_product(
                "https://shop.tiktok.com/vn/pdp/1737062736670590410",
                "1737062736670590410",
                "Áo thun Boxy",
                image,
            )
            ready = manager.update_fashion_product_copy(
                product.id,
                "Áo thun Boxy mặc đẹp quá.",
                "#thoitrang #aothun #boxy #outfit #tiktokshop",
            )
            video = root / "fashion_products" / "videos" / "boxy.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"video")
            with_video = manager.set_fashion_product_video(product.id, video)
            sent = manager.update_fashion_product_status(product.id, "sent")

        self.assertEqual(ready.status, "ready")
        self.assertIn("#thoitrang", ready.description)
        self.assertEqual(manager.resolve_fashion_product_video_path(with_video), video)
        self.assertEqual(sent.status, "sent")

    def test_dedicated_bot_accepts_tiktok_link_and_processes_it_in_the_background(self):
        client = mock.Mock()
        manager = mock.Mock()
        manager.project_root = Path(tempfile.gettempdir())

        class InlineExecutor:
            def submit(self, fn, *args):
                fn(*args)

        product = SimpleNamespace(
            product_name="Áo thun Boxy",
            product_id="1737062736670590410",
            description="Áo thun Boxy cực chất.\n#thoitrang #aothun #boxy #outfit #tiktokshop",
        )
        with mock.patch(
            "auto_tiktok_editor.app.fashion_bot.load_fashion_bot_settings",
            return_value=FashionBotSettings(token="fashion-token", allowed_chat_ids="123"),
        ), mock.patch(
            "auto_tiktok_editor.app.fashion_bot.receive_and_generate_fashion_product",
            return_value=product,
        ) as receive_product:
            service = FashionProductBotService(
                config=PipelineConfig(),
                client=client,
                manager=manager,
                executor=InlineExecutor(),
            )
            service.handle_update(
                {
                    "message": {
                        "chat": {"id": 123},
                        "text": "https://vt.tiktok.com/ZS9ByjCHbqEMg-fXJKc/",
                    }
                }
            )

        receive_product.assert_called_once()
        self.assertGreaterEqual(client.send_message.call_count, 2)
        manager.add_log.assert_called_once()
