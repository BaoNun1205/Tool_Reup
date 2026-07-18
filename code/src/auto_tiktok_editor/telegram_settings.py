from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTABLE_SETTINGS_FILENAME = "telegram_client_settings.json"


def _settings_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AutoTikTokEditor" / "telegram_settings.json"
    return Path.home() / ".auto_tiktok_editor" / "telegram_settings.json"


def _portable_settings_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name(PORTABLE_SETTINGS_FILENAME)
    return PROJECT_ROOT / PORTABLE_SETTINGS_FILENAME


def _prefer_portable_settings_file() -> bool:
    return bool(
        getattr(sys, "frozen", False)
        or _portable_settings_path().exists()
    )


@dataclass(frozen=True)
class TelegramRuntimeSettings:
    bot_token: str = ""
    delivery_chat_id: str = ""
    send_result_to_telegram: bool = False
    save_received_video_to_profile: bool = True
    video_cut_mode: str = "fixed"
    fixed_chunk_duration_seconds: float = 2.27
    scene_threshold: float = 0.35
    product_image_crop_ratio: str = "1:1"
    product_image_motion: str = "still"


def _float_setting(payload: dict[str, object], key: str, default: float) -> float:
    try:
        return float(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _video_cut_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"fixed", "scene", "original", "remove_background"} else "fixed"


def _product_image_crop_ratio(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("x", ":")
    return normalized if normalized in {"1:1", "4:3"} else "1:1"


def _product_image_motion(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"still", "zoom"} else "still"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _build_blob(value: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob()
    blob.cbData = len(value)
    blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    blob._buffer = buffer  # type: ignore[attr-defined]
    return blob


def _dpapi_protect(value: bytes) -> bytes:
    if not value:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source_blob = _build_blob(value)
    target_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source_blob),
        "AutoTikTokEditorTelegramSettings",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target_blob),
    ):
        raise OSError("Windows DPAPI could not encrypt Telegram settings on this machine.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _dpapi_unprotect(value: bytes) -> bytes:
    if not value:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source_blob = _build_blob(value)
    target_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target_blob),
    ):
        raise OSError("Windows DPAPI could not decrypt Telegram settings on this machine.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _settings_from_payload(payload: dict[str, object]) -> TelegramRuntimeSettings:
    return TelegramRuntimeSettings(
        bot_token=str(payload.get("bot_token") or "").strip(),
        delivery_chat_id=str(payload.get("delivery_chat_id") or "").strip(),
        send_result_to_telegram=bool(payload.get("send_result_to_telegram", False)),
        save_received_video_to_profile=bool(payload.get("save_received_video_to_profile", True)),
        video_cut_mode=_video_cut_mode(payload.get("video_cut_mode", "fixed")),
        fixed_chunk_duration_seconds=max(0.5, _float_setting(payload, "fixed_chunk_duration_seconds", 2.27)),
        scene_threshold=max(0.01, min(0.95, _float_setting(payload, "scene_threshold", 0.35))),
        product_image_crop_ratio=_product_image_crop_ratio(payload.get("product_image_crop_ratio", "1:1")),
        product_image_motion=_product_image_motion(payload.get("product_image_motion", "still")),
    )


def _load_portable_settings() -> TelegramRuntimeSettings | None:
    path = _portable_settings_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return _settings_from_payload(payload)
    return None


def load_telegram_runtime_settings() -> TelegramRuntimeSettings:
    portable_settings = _load_portable_settings()
    if portable_settings is not None:
        return portable_settings
    path = _settings_path()
    if not path.exists():
        return TelegramRuntimeSettings()
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(envelope, dict) and envelope.get("format") == "dpapi-v1":
            protected_payload = base64.b64decode(str(envelope["payload"]).encode("ascii"))
            payload = json.loads(_dpapi_unprotect(protected_payload).decode("utf-8"))
            if isinstance(payload, dict):
                return _settings_from_payload(payload)
            return TelegramRuntimeSettings()
        if isinstance(envelope, dict):
            return _settings_from_payload(envelope)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        try:
            path.unlink()
        except OSError:
            pass
    return TelegramRuntimeSettings()


def save_telegram_runtime_settings(settings: TelegramRuntimeSettings) -> Path:
    if _prefer_portable_settings_file():
        path = _portable_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "bot_token": settings.bot_token,
                    "delivery_chat_id": settings.delivery_chat_id,
                    "send_result_to_telegram": settings.send_result_to_telegram,
                    "save_received_video_to_profile": settings.save_received_video_to_profile,
                    "video_cut_mode": settings.video_cut_mode,
                    "fixed_chunk_duration_seconds": settings.fixed_chunk_duration_seconds,
                    "scene_threshold": settings.scene_threshold,
                    "product_image_crop_ratio": settings.product_image_crop_ratio,
                    "product_image_motion": settings.product_image_motion,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "bot_token": settings.bot_token,
            "delivery_chat_id": settings.delivery_chat_id,
            "send_result_to_telegram": settings.send_result_to_telegram,
            "save_received_video_to_profile": settings.save_received_video_to_profile,
            "video_cut_mode": settings.video_cut_mode,
            "fixed_chunk_duration_seconds": settings.fixed_chunk_duration_seconds,
            "scene_threshold": settings.scene_threshold,
            "product_image_crop_ratio": settings.product_image_crop_ratio,
            "product_image_motion": settings.product_image_motion,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    protected_payload = _dpapi_protect(payload)
    envelope = {
        "format": "dpapi-v1",
        "payload": base64.b64encode(protected_payload).decode("ascii"),
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def clear_telegram_runtime_settings() -> None:
    portable_path = _portable_settings_path()
    try:
        portable_path.unlink()
    except FileNotFoundError:
        pass
    path = _settings_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
