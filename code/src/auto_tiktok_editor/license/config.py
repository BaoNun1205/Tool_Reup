from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


def _default_cache_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AutoTikTokEditor" / "license_cache.json"
    return Path.home() / ".auto_tiktok_editor" / "license_cache.json"


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def _is_local_server_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _resolve_server_base_url() -> str:
    configured = (os.getenv("AUTO_EDITOR_LICENSE_SERVER_URL") or "").strip().rstrip("/")
    commercial_mode = _env_flag("AUTO_EDITOR_COMMERCIAL_MODE", True)
    if configured:
        if commercial_mode and _is_local_server_url(configured):
            raise ValueError(
                "Bản thương mại chưa được cấu hình license server online hợp lệ. "
                "AUTO_EDITOR_LICENSE_SERVER_URL không được trỏ về localhost."
            )
        return configured
    if commercial_mode:
        raise ValueError(
            "Bản thương mại chưa được cấu hình license server online. "
            "Hãy đặt AUTO_EDITOR_LICENSE_SERVER_URL tới server production trước khi chạy."
        )
    return "http://127.0.0.1:8787"


@dataclass(frozen=True)
class LicenseClientConfig:
    server_base_url: str
    cache_path: Path
    request_timeout_seconds: int = 10
    refresh_leeway_seconds: int = 300
    heartbeat_interval_seconds: int = 180
    offline_grace_hours: int = 48

    @classmethod
    def from_env(cls) -> "LicenseClientConfig":
        return cls(
            server_base_url=_resolve_server_base_url(),
            cache_path=Path(os.getenv("AUTO_EDITOR_LICENSE_CACHE_PATH", str(_default_cache_path()))),
            request_timeout_seconds=max(3, _env_int("AUTO_EDITOR_LICENSE_REQUEST_TIMEOUT_SECONDS", 10)),
            refresh_leeway_seconds=max(30, _env_int("AUTO_EDITOR_LICENSE_REFRESH_LEEWAY_SECONDS", 300)),
            heartbeat_interval_seconds=max(30, _env_int("AUTO_EDITOR_LICENSE_HEARTBEAT_SECONDS", 180)),
            offline_grace_hours=max(1, _env_int("AUTO_EDITOR_LICENSE_OFFLINE_GRACE_HOURS", 48)),
        )
