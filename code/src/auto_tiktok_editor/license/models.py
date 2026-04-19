from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DeviceIdentity:
    fingerprint: str
    label: str
    platform_name: str = "windows"
    app_version: str | None = None


@dataclass(frozen=True)
class LicenseTokenBundle:
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime
    session_id: str
    account_id: str
    username: str
    license_id: str
    license_code: str
    plan_name: str
    license_expires_at: datetime
    device_id: str
    device_fingerprint: str
    public_key_b64: str
    server_base_url: str
    cached_at: datetime
    server_time: datetime
    last_verified_at: datetime
    offline_grace_expires_at: datetime


@dataclass(frozen=True)
class VerifiedLicenseSession:
    account_id: str
    username: str
    license_id: str
    session_id: str
    device_id: str
    plan_name: str
    license_expires_at: datetime
    access_token_expires_at: datetime
    raw_payload: dict[str, Any]
