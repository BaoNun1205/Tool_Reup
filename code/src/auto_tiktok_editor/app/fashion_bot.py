"""A dedicated Telegram bot that accepts only TikTok Shop product links for Fashion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import threading
import time
from typing import Any

from auto_tiktok_editor.app.fashion_products import receive_and_generate_fashion_product
from auto_tiktok_editor.app.telegram_bot import TelegramBotClient, extract_urls_from_caption
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.fashion_bot_settings import load_fashion_bot_settings, parse_allowed_chat_ids
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager


class FashionProductBotService:
    """Poll a separate bot token and turn each TikTok Shop link into a Fashion row."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        client: TelegramBotClient | None = None,
        manager: TikTokProfileManager | None = None,
        logger: logging.Logger | None = None,
        executor: ThreadPoolExecutor | None = None,
        bot_token: str | None = None,
        allowed_chat_ids: tuple[int, ...] | None = None,
    ) -> None:
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor.fashion_bot")
        settings = load_fashion_bot_settings()
        resolved_token = str(bot_token or settings.token or "").strip()
        if not resolved_token:
            raise RuntimeError("Chưa có token Bot Fashion. Hãy cấu hình trong tab Fashion.")
        self.allowed_chat_ids = tuple(allowed_chat_ids) if allowed_chat_ids is not None else parse_allowed_chat_ids(settings.allowed_chat_ids)
        self.client = client or TelegramBotClient(resolved_token, logger=self.logger)
        self.manager = manager or TikTokProfileManager()
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="fashion-product")
        self._owns_executor = executor is None
        self._stop_event = threading.Event()
        self._poll_offset_path = Path(self.manager.project_root) / "fashion_products" / "_telegram_poll_offset.txt"

    def serve_forever(self) -> None:
        offset = self._load_poll_offset()
        self.logger.info("Fashion product bot started.")
        try:
            while not self._stop_event.is_set():
                try:
                    updates = self.client.get_updates(offset=offset, timeout_seconds=self.config.telegram_poll_timeout_seconds)
                    for update in updates:
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = update_id + 1
                            self._save_poll_offset(offset)
                        self.handle_update(update)
                except Exception as exc:
                    self.logger.warning("Fashion bot polling failed: %s", exc)
                    self._stop_event.wait(max(1, int(self.config.telegram_poll_interval_seconds)))
        finally:
            if self._owns_executor:
                self.executor.shutdown(wait=False)

    def stop(self) -> None:
        self._stop_event.set()

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            self.client.send_message(chat_id, "Chat này chưa được cấp quyền dùng Bot Fashion.")
            return
        text = str(message.get("text") or message.get("caption") or "").strip()
        if text.lower() in {"/start", "/help"}:
            self.client.send_message(chat_id, self._instruction_text())
            return
        urls = extract_urls_from_caption(text)
        if not urls:
            self.client.send_message(chat_id, self._instruction_text())
            return
        product_url = urls[0]
        if "tiktok.com" not in product_url.lower():
            self.client.send_message(chat_id, "Bot Fashion chỉ nhận link TikTok Shop hoặc link rút gọn vt.tiktok.com.")
            return
        self.client.send_message(chat_id, "Đã nhận link. Đang lấy ảnh, tên sản phẩm và viết mô tả bằng Gemini...")
        self.executor.submit(self._receive_product, chat_id, product_url)

    def _receive_product(self, chat_id: int, product_url: str) -> None:
        try:
            product = receive_and_generate_fashion_product(self.manager, product_url)
            self.manager.add_log(
                "info",
                "fashion_product_received",
                "Đã nhận sản phẩm Fashion '%s' (%s)." % (product.product_name, product.product_id),
            )
            self.client.send_message(
                chat_id,
                "Đã thêm vào Fashion.\n\n%s\n\n%s\nProduct ID: %s"
                % (product.product_name, product.description, product.product_id),
            )
        except Exception as exc:
            self.logger.warning("Fashion product processing failed: %s", exc)
            self.client.send_message(chat_id, "Không thể xử lý link sản phẩm: %s" % exc)

    @staticmethod
    def _instruction_text() -> str:
        return (
            "Gửi một link TikTok Shop hoặc vt.tiktok.com.\n"
            "Bot sẽ tự lấy tên, ảnh, Product ID và tạo mô tả Fashion bằng Gemini."
        )

    def _load_poll_offset(self) -> int | None:
        try:
            raw_value = self._poll_offset_path.read_text(encoding="utf-8").strip()
            return int(raw_value) if raw_value else None
        except (OSError, ValueError):
            return None

    def _save_poll_offset(self, offset: int) -> None:
        try:
            self._poll_offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._poll_offset_path.write_text(str(offset), encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Could not save Fashion bot poll offset: %s", exc)
