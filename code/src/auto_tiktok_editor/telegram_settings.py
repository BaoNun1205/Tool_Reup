from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


def _settings_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AutoTikTokEditor" / "telegram_settings.json"
    return Path.home() / ".auto_tiktok_editor" / "telegram_settings.json"


@dataclass(frozen=True)
class TelegramRuntimeSettings:
    bot_token: str = ""
    delivery_chat_id: str = ""


def load_telegram_runtime_settings() -> TelegramRuntimeSettings:
    path = _settings_path()
    if not path.exists():
        return TelegramRuntimeSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return TelegramRuntimeSettings()
    if not isinstance(payload, dict):
        return TelegramRuntimeSettings()
    return TelegramRuntimeSettings(
        bot_token=str(payload.get("bot_token") or "").strip(),
        delivery_chat_id=str(payload.get("delivery_chat_id") or "").strip(),
    )


def save_telegram_runtime_settings(settings: TelegramRuntimeSettings) -> Path:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "bot_token": settings.bot_token,
                "delivery_chat_id": settings.delivery_chat_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def clear_telegram_runtime_settings() -> None:
    path = _settings_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
