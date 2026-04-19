"""Helpers for sending reviewed session outputs to Telegram chats."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from auto_tiktok_editor.app.telegram_bot import TelegramBotClient
from auto_tiktok_editor.commercial_runtime import ensure_local_telegram_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ItemProcessResult, SessionResult


TELEGRAM_CAPTION_MAX_CHARS = 1024


class TelegramDeliveryService(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        client: Optional[TelegramBotClient] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        ensure_local_telegram_allowed(self.config, surface="telegram-delivery")
        self.logger = logger or logging.getLogger("auto_tiktok_editor.telegram.delivery")
        self.client = client or TelegramBotClient(self.config.telegram_bot_token, logger=self.logger)

    def send_session_result(self, result: SessionResult, chat_id: int) -> Dict[str, object]:
        if result is None:
            raise ValueError("Không có session result để gửi qua Telegram.")
        completed_items = [
            item for item in sorted(result.items, key=lambda current: current.item_index)
            if item.status == "completed"
        ]
        if not completed_items:
            raise ValueError("Session này chưa có video hoàn tất để gửi qua Telegram.")

        sent_count = 0
        skipped = []
        for item in completed_items:
            video_path = self._item_video_path(item)
            if video_path is None or not video_path.exists():
                skipped.append(item.row_id)
                continue
            self.client.send_video(chat_id, video_path, caption=self._build_caption(item))
            sent_count += 1

        if sent_count == 0:
            raise ValueError("Không tìm thấy file video hợp lệ để gửi qua Telegram.")

        summary_text = "Đã gửi %s video sang Telegram." % sent_count
        if skipped:
            summary_text += " Bỏ qua %s item thiếu file output." % len(skipped)
        self.client.send_message(chat_id, summary_text)
        return {
            "chat_id": chat_id,
            "sent_count": sent_count,
            "skipped_items": skipped,
        }

    def _item_video_path(self, item: ItemProcessResult) -> Optional[Path]:
        if item.artifacts and item.artifacts.final_video_path:
            return Path(item.artifacts.final_video_path)
        return None

    def _build_caption(self, item: ItemProcessResult) -> str:
        title = str(item.metadata.get("source_title") or "").strip()
        if not title:
            title = "Video đã edit xong."
        if len(title) > TELEGRAM_CAPTION_MAX_CHARS:
            return title[: TELEGRAM_CAPTION_MAX_CHARS - 1].rstrip() + "…"
        return title
