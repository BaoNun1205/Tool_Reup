"""Local configuration for the Telegram bot dedicated to Fashion product links."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings


_ORGANIZATION = "AutoTikTokEditor"
_APPLICATION = "TikTokProfileManager"
_TOKEN_KEY = "fashion_bot/token"
_CHAT_IDS_KEY = "fashion_bot/allowed_chat_ids"


@dataclass(frozen=True)
class FashionBotSettings:
    token: str = ""
    allowed_chat_ids: str = ""


def load_fashion_bot_settings() -> FashionBotSettings:
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    return FashionBotSettings(
        token=str(settings.value(_TOKEN_KEY, "") or "").strip(),
        allowed_chat_ids=str(settings.value(_CHAT_IDS_KEY, "") or "").strip(),
    )


def save_fashion_bot_settings(token: str, allowed_chat_ids: str) -> None:
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue(_TOKEN_KEY, str(token or "").strip())
    settings.setValue(_CHAT_IDS_KEY, str(allowed_chat_ids or "").strip())
    settings.sync()


def parse_allowed_chat_ids(value: str) -> tuple[int, ...]:
    ids = []
    for raw_value in str(value or "").replace(";", ",").split(","):
        raw_value = raw_value.strip()
        if not raw_value:
            continue
        try:
            ids.append(int(raw_value))
        except ValueError as exc:
            raise ValueError("Chat ID Fashion phải là số, cách nhau bằng dấu phẩy.") from exc
    return tuple(dict.fromkeys(ids))
