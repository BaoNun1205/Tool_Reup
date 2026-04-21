from __future__ import annotations

from functools import lru_cache
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from license_server.app.admin_web import router as admin_web_router
from license_server.app.config import LicenseServerConfig
from license_server.app.database import create_all, get_config, get_db, session_scope
from license_server.app.schemas import (
    AdminCreateUserRequest,
    AdminDeviceListResponse,
    AdminDeviceSummary,
    AdminExtendLicenseRequest,
    AdminIssueLicenseRequest,
    AdminLicenseListResponse,
    AdminLicenseSummary,
    AdminRevokeRequest,
    AdminResetPasswordRequest,
    AdminSessionListResponse,
    AdminSessionSummary,
    AdminSetLicenseStatusRequest,
    AdminSetUserStatusRequest,
    AdminUserListResponse,
    AdminUserSummary,
    AccountSnapshotResponse,
    AuthTokensResponse,
    HeartbeatRequest,
    LoginRequest,
    PublicKeyResponse,
    RefreshRequest,
)
from license_server.app.security import TokenSigner, utcnow
from license_server.app.services import AdminService, AuthError, AuthService, LicenseError, LicenseService


app = FastAPI(title="Auto TikTok Editor License Server", version="0.1.0")
create_all()
app.include_router(admin_web_router)
logger = logging.getLogger("license_server.bootstrap")


def _bootstrap_admin_account() -> None:
    config = get_config()
    username = config.bootstrap_admin_username.strip()
    password = config.bootstrap_admin_password
    if not username or not password:
        return
    with session_scope() as db:
        service = LicenseService(db, config)
        user, created = service.ensure_admin_user(username, password)
        logger.warning(
            "Bootstrap admin %s for username=%s (user_id=%s).",
            "created" if created else "updated",
            user.username,
            user.id,
        )


@app.on_event("startup")
def startup_bootstrap() -> None:
    _bootstrap_admin_account()


@app.get("/health")
def healthcheck(db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> dict:
    try:
        probe = db.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is unavailable.") from exc
    return {
        "status": "ok",
        "database": "ok",
        "db_probe": probe,
        "server_time": utcnow(),
        "public_base_url": config.public_base_url,
    }


@lru_cache(maxsize=1)
def get_signer(config: LicenseServerConfig = Depends(get_config)) -> TokenSigner:
    return TokenSigner(config)


def _raise_auth(exc: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


def _raise_license(exc: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header.")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header must use Bearer token.")
    return authorization[len(prefix) :].strip()


def _current_user(authorization: str | None, db: Session, config: LicenseServerConfig, signer: TokenSigner):
    token = _bearer_token(authorization)
    token_payload = signer.verify_access_token(token)
    service = AuthService(db, config, signer)
    session = service.license_service.get_session(token_payload["sid"])
    if session.revoked_at is not None:
        raise AuthError("Session is no longer active.")
    return session.user


def _require_admin_user(authorization: str | None, db: Session, config: LicenseServerConfig, signer: TokenSigner):
    try:
        user = _current_user(authorization, db, config, signer)
    except (AuthError, ValueError, LicenseError) as exc:
        _raise_auth(exc)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access is required.")
    return user


def _serialize_user(admin_service: AdminService, user) -> AdminUserSummary:
    licenses = [record for record in user.licenses]
    licenses.sort(key=lambda record: (record.expires_at, record.created_at), reverse=True)
    current_license = licenses[0] if licenses else None
    active_devices = [device for device in user.devices if device.revoked_at is None and device.is_active]
    return AdminUserSummary(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        active_license_id=current_license.id if current_license else None,
        active_license_code=current_license.license_code if current_license else None,
        active_license_status=current_license.status if current_license else None,
        active_license_expires_at=current_license.expires_at if current_license else None,
        device_count=len(active_devices),
    )


def _serialize_license(record) -> AdminLicenseSummary:
    return AdminLicenseSummary(
        id=record.id,
        user_id=record.user_id,
        username=record.user.username,
        license_code=record.license_code,
        status=record.status,
        plan_name=record.plan_name,
        expires_at=record.expires_at,
        max_devices=record.max_devices,
        max_concurrent_sessions=record.max_concurrent_sessions,
        notes=record.notes,
        created_at=record.created_at,
    )


def _serialize_device(record) -> AdminDeviceSummary:
    return AdminDeviceSummary(
        id=record.id,
        user_id=record.user_id,
        device_fingerprint=record.device_fingerprint,
        device_label=record.device_label,
        platform_name=record.platform_name,
        app_version=record.app_version,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
        revoked_at=record.revoked_at,
        is_active=record.is_active,
    )


def _serialize_session(record) -> AdminSessionSummary:
    return AdminSessionSummary(
        id=record.id,
        user_id=record.user_id,
        license_id=record.license_id,
        device_id=record.device_id,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        refresh_expires_at=record.refresh_expires_at,
        revoked_at=record.revoked_at,
        revoke_reason=record.revoke_reason,
    )


@app.get("/api/v1/meta/public-key", response_model=PublicKeyResponse)
def public_key(signer: TokenSigner = Depends(get_signer)) -> PublicKeyResponse:
    return PublicKeyResponse(public_key_b64=signer.public_key_b64, server_time=utcnow())


@app.post("/api/v1/auth/login", response_model=AuthTokensResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config), signer: TokenSigner = Depends(get_signer)) -> AuthTokensResponse:
    service = AuthService(db, config, signer)
    try:
        bundle = service.login(
            username=payload.username,
            password=payload.password,
            device_fingerprint=payload.device.device_fingerprint,
            device_label=payload.device.device_label,
            platform_name=payload.device.platform_name,
            app_version=payload.device.app_version,
        )
    except AuthError as exc:
        _raise_auth(exc)
    except LicenseError as exc:
        _raise_license(exc)
    return AuthTokensResponse(
        access_token=bundle.access_token,
        access_token_expires_at=bundle.access_token_expires_at,
        refresh_token=bundle.refresh_token,
        refresh_token_expires_at=bundle.refresh_token_expires_at,
        server_time=utcnow(),
        public_key_b64=signer.public_key_b64,
        session_id=bundle.session_id,
        account_id=bundle.account_id,
        username=bundle.username,
        license_id=bundle.license_id,
        license_code=bundle.license_code,
        plan_name=bundle.plan_name,
        license_expires_at=bundle.license_expires_at,
        device_id=bundle.device_id,
        device_fingerprint=bundle.device_fingerprint,
    )


@app.post("/api/v1/auth/refresh", response_model=AuthTokensResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config), signer: TokenSigner = Depends(get_signer)) -> AuthTokensResponse:
    service = AuthService(db, config, signer)
    try:
        bundle = service.refresh(refresh_token=payload.refresh_token, device_fingerprint=payload.device_fingerprint)
    except AuthError as exc:
        _raise_auth(exc)
    except LicenseError as exc:
        _raise_license(exc)
    return AuthTokensResponse(
        access_token=bundle.access_token,
        access_token_expires_at=bundle.access_token_expires_at,
        refresh_token=bundle.refresh_token,
        refresh_token_expires_at=bundle.refresh_token_expires_at,
        server_time=utcnow(),
        public_key_b64=signer.public_key_b64,
        session_id=bundle.session_id,
        account_id=bundle.account_id,
        username=bundle.username,
        license_id=bundle.license_id,
        license_code=bundle.license_code,
        plan_name=bundle.plan_name,
        license_expires_at=bundle.license_expires_at,
        device_id=bundle.device_id,
        device_fingerprint=bundle.device_fingerprint,
    )


@app.post("/api/v1/auth/heartbeat")
def heartbeat(payload: HeartbeatRequest, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config), signer: TokenSigner = Depends(get_signer), authorization: str | None = Header(default=None)) -> dict:
    token = _bearer_token(authorization)
    try:
        token_payload = signer.verify_access_token(token)
        if token_payload.get("sid") != payload.session_id:
            raise AuthError("Session mismatch.")
        service = AuthService(db, config, signer)
        service.heartbeat(session_id=payload.session_id)
    except LicenseError as exc:
        _raise_license(exc)
    except (AuthError, ValueError) as exc:
        _raise_auth(exc)
    return {"status": "ok", "server_time": utcnow()}


@app.post("/api/v1/auth/logout")
def logout(db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config), signer: TokenSigner = Depends(get_signer), authorization: str | None = Header(default=None)) -> dict:
    token = _bearer_token(authorization)
    try:
        token_payload = signer.verify_access_token(token)
        service = AuthService(db, config, signer)
        service.logout(session_id=token_payload["sid"])
    except (AuthError, ValueError) as exc:
        _raise_auth(exc)
    return {"status": "ok"}


@app.get("/api/v1/auth/me", response_model=AccountSnapshotResponse)
def me(db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config), signer: TokenSigner = Depends(get_signer), authorization: str | None = Header(default=None)) -> AccountSnapshotResponse:
    token = _bearer_token(authorization)
    try:
        token_payload = signer.verify_access_token(token)
        service = AuthService(db, config, signer)
        snapshot = service.snapshot_for_token(token_payload)
    except (AuthError, ValueError, LicenseError) as exc:
        _raise_auth(exc)
    return AccountSnapshotResponse(server_time=utcnow(), **snapshot)


@app.get("/api/v1/admin/users", response_model=AdminUserListResponse)
def admin_list_users(
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminUserListResponse:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return AdminUserListResponse(
        users=[_serialize_user(service, user) for user in service.list_users()],
        server_time=utcnow(),
    )


@app.post("/api/v1/admin/users", response_model=AdminUserSummary)
def admin_create_user(
    payload: AdminCreateUserRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminUserSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    try:
        user = service.create_user(username=payload.username, password=payload.password, is_admin=payload.is_admin)
    except LicenseError as exc:
        _raise_license(exc)
    return _serialize_user(service, user)


@app.post("/api/v1/admin/users/{user_id}/status", response_model=AdminUserSummary)
def admin_set_user_status(
    user_id: str,
    payload: AdminSetUserStatusRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminUserSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    user = service.set_user_status(user_id=user_id, is_active=payload.is_active)
    return _serialize_user(service, user)


@app.post("/api/v1/admin/users/{user_id}/password", response_model=AdminUserSummary)
def admin_reset_user_password(
    user_id: str,
    payload: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminUserSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    user = service.reset_user_password(user_id=user_id, password=payload.password)
    return _serialize_user(service, user)


@app.get("/api/v1/admin/licenses", response_model=AdminLicenseListResponse)
def admin_list_licenses(
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminLicenseListResponse:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return AdminLicenseListResponse(
        licenses=[_serialize_license(record) for record in service.list_licenses()],
        server_time=utcnow(),
    )


@app.post("/api/v1/admin/licenses/issue", response_model=AdminLicenseSummary)
def admin_issue_license(
    payload: AdminIssueLicenseRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminLicenseSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    try:
        record = service.issue_license(
            user_id=payload.user_id,
            plan_name=payload.plan_name,
            days=payload.days,
            max_devices=payload.max_devices,
            max_concurrent_sessions=payload.max_concurrent_sessions or payload.max_devices,
            notes=payload.notes,
        )
    except LicenseError as exc:
        _raise_license(exc)
    return _serialize_license(record)


@app.post("/api/v1/admin/licenses/{license_id}/extend", response_model=AdminLicenseSummary)
def admin_extend_license(
    license_id: str,
    payload: AdminExtendLicenseRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminLicenseSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    record = service.extend_license(license_id=license_id, extra_days=payload.extra_days)
    return _serialize_license(record)


@app.post("/api/v1/admin/licenses/{license_id}/status", response_model=AdminLicenseSummary)
def admin_set_license_status(
    license_id: str,
    payload: AdminSetLicenseStatusRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminLicenseSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    record = service.set_license_status(license_id=license_id, status=payload.status)
    return _serialize_license(record)


@app.get("/api/v1/admin/users/{user_id}/devices", response_model=AdminDeviceListResponse)
def admin_list_devices(
    user_id: str,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminDeviceListResponse:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return AdminDeviceListResponse(
        devices=[_serialize_device(record) for record in service.list_devices(user_id)],
        server_time=utcnow(),
    )


@app.post("/api/v1/admin/devices/{device_id}/revoke", response_model=AdminDeviceSummary)
def admin_revoke_device(
    device_id: str,
    payload: AdminRevokeRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminDeviceSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return _serialize_device(service.revoke_device(device_id=device_id, reason=payload.reason))


@app.get("/api/v1/admin/users/{user_id}/sessions", response_model=AdminSessionListResponse)
def admin_list_sessions(
    user_id: str,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminSessionListResponse:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return AdminSessionListResponse(
        sessions=[_serialize_session(record) for record in service.list_sessions(user_id)],
        server_time=utcnow(),
    )


@app.post("/api/v1/admin/sessions/{session_id}/revoke", response_model=AdminSessionSummary)
def admin_revoke_session(
    session_id: str,
    payload: AdminRevokeRequest,
    db: Session = Depends(get_db),
    config: LicenseServerConfig = Depends(get_config),
    signer: TokenSigner = Depends(get_signer),
    authorization: str | None = Header(default=None),
) -> AdminSessionSummary:
    _require_admin_user(authorization, db, config, signer)
    service = AdminService(db, config)
    return _serialize_session(service.revoke_session(session_id=session_id, reason=payload.reason))
