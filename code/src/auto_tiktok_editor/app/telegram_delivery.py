"""Helpers for sending reviewed session outputs to Telegram chats."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from auto_tiktok_editor.app.telegram_bot import TelegramBotClient
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ItemProcessResult, SessionResult


TELEGRAM_CAPTION_MAX_CHARS = 1024


class TelegramDeliveryService(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        client: Optional[TelegramBotClient] = None,
        client_factory: Optional[Callable[[str], TelegramBotClient]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor.telegram.delivery")
        if client is not None:
            self.client = client
        elif self.config.telegram_bot_token:
            self.client = TelegramBotClient(self.config.telegram_bot_token, logger=self.logger)
        else:
            self.client = None
        self.client_factory = client_factory or (lambda token: TelegramBotClient(token, logger=self.logger))
        self._clients_by_token = {}  # type: Dict[str, TelegramBotClient]

    def send_session_result(self, result: SessionResult, chat_id: Optional[int] = None) -> Dict[str, object]:
        if result is None:
            raise ValueError("Không có session result để gửi qua Telegram.")
        completed_items = [
            item for item in sorted(result.items, key=lambda current: current.item_index)
            if item.status == "completed"
        ]
        if not completed_items:
            raise ValueError("Session này chưa có video hoàn tất để gửi qua Telegram.")

        send_plan = []
        skipped = []
        delivery_errors = []
        for item in completed_items:
            video_path = self._item_video_path(item)
            if video_path is None or not video_path.exists():
                skipped.append(item.row_id)
                continue
            try:
                bot_token, resolved_chat_id = self._resolve_delivery_target(item, chat_id)
            except ValueError as exc:
                delivery_errors.append("Item %s: %s" % (item.item_index + 1, exc))
                continue
            send_plan.append((item, video_path, bot_token, resolved_chat_id))

        if delivery_errors:
            raise ValueError("Thiếu cấu hình Telegram hợp lệ: %s" % " | ".join(delivery_errors))
        if not send_plan:
            raise ValueError("Không tìm thấy file video hợp lệ để gửi qua Telegram.")

        sent_count = 0
        sent_by_target = {}  # type: Dict[Tuple[str, int], int]
        for item, video_path, bot_token, resolved_chat_id in send_plan:
            client = self._client_for_token(bot_token)
            client.send_document(
                resolved_chat_id,
                video_path,
                caption=self._build_caption(item),
                filename="video_final.mp4",
            )
            product_id = self._item_product_id(item)
            if product_id:
                client.send_message(resolved_chat_id, product_id)
            sent_count += 1
            target_key = (bot_token, resolved_chat_id)
            sent_by_target[target_key] = sent_by_target.get(target_key, 0) + 1

        for (bot_token, resolved_chat_id), target_sent_count in sent_by_target.items():
            summary_text = "Đã gửi %s video sang Telegram." % target_sent_count
            if skipped:
                summary_text += " Bỏ qua %s item thiếu file output." % len(skipped)
            self._client_for_token(bot_token).send_message(resolved_chat_id, summary_text)

        chat_ids = sorted({target_chat_id for _token, target_chat_id in sent_by_target.keys()})
        return {
            "chat_id": chat_ids[0] if len(chat_ids) == 1 else ", ".join(str(value) for value in chat_ids),
            "chat_ids": chat_ids,
            "target_count": len(sent_by_target),
            "sent_count": sent_count,
            "skipped_items": skipped,
        }

    def _resolve_delivery_target(self, item: ItemProcessResult, fallback_chat_id: Optional[int]) -> Tuple[str, int]:
        bot_token = str(getattr(item, "telegram_bot_token", "") or self.config.telegram_bot_token or "").strip()
        chat_id_value = str(
            getattr(item, "telegram_chat_id", "")
            or (str(fallback_chat_id) if fallback_chat_id is not None else "")
            or self.config.telegram_delivery_chat_id
            or ""
        ).strip()
        resolved_chat_id = self._normalize_chat_id(chat_id_value)
        if resolved_chat_id is None:
            raise ValueError("Telegram Chat ID không hợp lệ.")
        if not bot_token and self.client is None:
            raise ValueError("Telegram Bot Token không hợp lệ.")
        return bot_token, resolved_chat_id

    def _normalize_chat_id(self, value: str) -> Optional[int]:
        try:
            return int(str(value or "").strip())
        except (TypeError, ValueError):
            return None

    def _client_for_token(self, bot_token: str) -> TelegramBotClient:
        if self.client is not None and (not bot_token or bot_token == self.config.telegram_bot_token):
            return self.client
        if not bot_token:
            raise ValueError("Telegram Bot Token không hợp lệ.")
        if bot_token not in self._clients_by_token:
            self._clients_by_token[bot_token] = self.client_factory(bot_token)
        return self._clients_by_token[bot_token]

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

    def _item_product_id(self, item: ItemProcessResult) -> str:
        return str(item.metadata.get("product_id") or "").strip()
