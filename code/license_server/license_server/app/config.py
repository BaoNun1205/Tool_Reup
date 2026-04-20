from __future__ import annotations

from dataclasses import dataclass
import base64
import os
from pathlib import Path
from typing import Optional


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _normalize_database_url(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized[len("postgres://") :]
    if normalized.startswith("postgresql://") and not normalized.startswith("postgresql+"):
        return "postgresql+psycopg://" + normalized[len("postgresql://") :]
    return normalized


@dataclass(frozen=True)
class LicenseServerConfig:
    database_url: str = "sqlite:///./license_server.db"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_hours: int = 24
    session_stale_minutes: int = 10
    signing_seed_b64: str = ""
    signing_seed_path: str = ""
    public_base_url: str = "http://127.0.0.1:8787"
    admin_session_secret: str = ""
    admin_session_cookie_name: str = "auto_editor_admin_session"
    admin_session_ttl_hours: int = 12
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    @classmethod
    def from_env(cls) -> "LicenseServerConfig":
        default_db = "sqlite:///%s" % (Path.cwd() / "license_server.db").resolve().as_posix()
        return cls(
            database_url=_normalize_database_url(_env_str("AUTO_EDITOR_LICENSE_DATABASE_URL", default_db)),
            access_token_ttl_minutes=max(5, _env_int("AUTO_EDITOR_LICENSE_ACCESS_TOKEN_TTL_MINUTES", 30)),
            refresh_token_ttl_hours=max(1, _env_int("AUTO_EDITOR_LICENSE_REFRESH_TOKEN_TTL_HOURS", 24)),
            session_stale_minutes=max(1, _env_int("AUTO_EDITOR_LICENSE_SESSION_STALE_MINUTES", 10)),
            signing_seed_b64=_env_str("AUTO_EDITOR_LICENSE_SIGNING_SEED_B64", ""),
            signing_seed_path=_env_str(
                "AUTO_EDITOR_LICENSE_SIGNING_SEED_PATH",
                str((Path.cwd() / ".auto_editor_license_signing_seed.b64").resolve()),
            ),
            public_base_url=_env_str("AUTO_EDITOR_LICENSE_PUBLIC_BASE_URL", "http://127.0.0.1:8787"),
            admin_session_secret=_env_str("AUTO_EDITOR_LICENSE_ADMIN_SESSION_SECRET", ""),
            admin_session_cookie_name=_env_str("AUTO_EDITOR_LICENSE_ADMIN_SESSION_COOKIE_NAME", "auto_editor_admin_session"),
            admin_session_ttl_hours=max(1, _env_int("AUTO_EDITOR_LICENSE_ADMIN_SESSION_TTL_HOURS", 12)),
            bootstrap_admin_username=_env_str("AUTO_EDITOR_BOOTSTRAP_ADMIN_USERNAME", ""),
            bootstrap_admin_password=_env_str("AUTO_EDITOR_BOOTSTRAP_ADMIN_PASSWORD", ""),
        )

    def load_signing_seed(self) -> Optional[bytes]:
        if not self.signing_seed_b64:
            if not self.signing_seed_path:
                return None
            seed_path = Path(self.signing_seed_path)
            if not seed_path.exists():
                return None
            try:
                encoded = seed_path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not encoded:
                return None
            return base64.b64decode(encoded.encode("ascii"))
        return base64.b64decode(self.signing_seed_b64.encode("ascii"))

    def persist_signing_seed(self, seed: bytes) -> None:
        if self.signing_seed_b64 or not self.signing_seed_path:
            return
        seed_path = Path(self.signing_seed_path)
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(base64.b64encode(seed).decode("ascii"), encoding="utf-8")

    def load_admin_session_secret(self) -> str:
        if self.admin_session_secret:
            return self.admin_session_secret
        if self.signing_seed_b64:
            return self.signing_seed_b64
        return "auto-editor-admin-dev-secret"
