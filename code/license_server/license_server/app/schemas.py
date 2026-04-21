from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DeviceRegistrationRequest(BaseModel):
    device_fingerprint: str = Field(min_length=16, max_length=128)
    device_label: str = Field(min_length=1, max_length=128)
    platform_name: str = Field(default="windows", min_length=2, max_length=32)
    app_version: Optional[str] = Field(default=None, max_length=32)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    device: DeviceRegistrationRequest


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)
    device_fingerprint: str = Field(min_length=16, max_length=128)


class HeartbeatRequest(BaseModel):
    session_id: str = Field(min_length=36, max_length=36)


class AuthTokensResponse(BaseModel):
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime
    server_time: datetime
    public_key_b64: str
    session_id: str
    account_id: str
    username: str
    license_id: str
    license_code: str
    plan_name: str
    license_expires_at: datetime
    device_id: str
    device_fingerprint: str


class AccountSnapshotResponse(BaseModel):
    account_id: str
    username: str
    license_id: str
    license_code: str
    plan_name: str
    license_expires_at: datetime
    device_id: str
    device_label: str
    server_time: datetime


class PublicKeyResponse(BaseModel):
    algorithm: str = "EdDSA"
    public_key_b64: str
    server_time: datetime


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    is_admin: bool = False


class AdminIssueLicenseRequest(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)
    plan_name: str = Field(default="standard", min_length=1, max_length=64)
    days: int = Field(ge=1, le=3650)
    max_devices: int = Field(default=1, ge=1, le=3)
    max_concurrent_sessions: Optional[int] = Field(default=None, ge=1, le=3)
    notes: Optional[str] = Field(default=None, max_length=2000)


class AdminExtendLicenseRequest(BaseModel):
    extra_days: int = Field(ge=1, le=3650)


class AdminSetLicenseStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled|expired)$")


class AdminSetUserStatusRequest(BaseModel):
    is_active: bool


class AdminResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=256)


class AdminRevokeRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=128)


class AdminUserSummary(BaseModel):
    id: str
    username: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    active_license_id: Optional[str] = None
    active_license_code: Optional[str] = None
    active_license_status: Optional[str] = None
    active_license_expires_at: Optional[datetime] = None
    device_count: int = 0


class AdminLicenseSummary(BaseModel):
    id: str
    user_id: str
    username: str
    license_code: str
    status: str
    plan_name: str
    expires_at: datetime
    max_devices: int
    max_concurrent_sessions: int
    notes: Optional[str] = None
    created_at: datetime


class AdminDeviceSummary(BaseModel):
    id: str
    user_id: str
    device_fingerprint: str
    device_label: str
    platform_name: str
    app_version: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime
    revoked_at: Optional[datetime] = None
    is_active: bool


class AdminSessionSummary(BaseModel):
    id: str
    user_id: str
    license_id: str
    device_id: str
    created_at: datetime
    last_seen_at: datetime
    refresh_expires_at: datetime
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserSummary]
    server_time: datetime


class AdminLicenseListResponse(BaseModel):
    licenses: list[AdminLicenseSummary]
    server_time: datetime


class AdminDeviceListResponse(BaseModel):
    devices: list[AdminDeviceSummary]
    server_time: datetime


class AdminSessionListResponse(BaseModel):
    sessions: list[AdminSessionSummary]
    server_time: datetime
