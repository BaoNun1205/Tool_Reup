from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


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
            server_base_url=(os.getenv("AUTO_EDITOR_LICENSE_SERVER_URL", "http://127.0.0.1:8787")).rstrip("/"),
            cache_path=Path(os.getenv("AUTO_EDITOR_LICENSE_CACHE_PATH", str(_default_cache_path()))),
            request_timeout_seconds=max(3, _env_int("AUTO_EDITOR_LICENSE_REQUEST_TIMEOUT_SECONDS", 10)),
            refresh_leeway_seconds=max(30, _env_int("AUTO_EDITOR_LICENSE_REFRESH_LEEWAY_SECONDS", 300)),
            heartbeat_interval_seconds=max(30, _env_int("AUTO_EDITOR_LICENSE_HEARTBEAT_SECONDS", 180)),
            offline_grace_hours=max(1, _env_int("AUTO_EDITOR_LICENSE_OFFLINE_GRACE_HOURS", 48)),
        )
