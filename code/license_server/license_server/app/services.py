from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from license_server.app.config import LicenseServerConfig
from license_server.app.models import AuditLogRecord, DeviceRecord, LicenseRecord, LoginSessionRecord, UserAccount
from license_server.app.security import (
    TokenSigner,
    create_license_code,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    utcnow,
    verify_password,
)


class AuthError(Exception):
    pass


class LicenseError(Exception):
    pass


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _active_session_query(license_id: str, stale_before) -> Select[tuple[LoginSessionRecord]]:
    return select(LoginSessionRecord).where(
        LoginSessionRecord.license_id == license_id,
        LoginSessionRecord.revoked_at.is_(None),
        LoginSessionRecord.refresh_expires_at > utcnow(),
        LoginSessionRecord.last_seen_at >= stale_before,
    )


@dataclass
class AuthBundle:
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


class LicenseService:
    def __init__(self, db: Session, config: LicenseServerConfig | None):
        self.db = db
        self.config = config

    def get_user_by_username(self, username: str) -> Optional[UserAccount]:
        statement = select(UserAccount).where(UserAccount.username == username.strip().lower())
        return self.db.execute(statement).scalar_one_or_none()

    def create_user(self, username: str, password: str, *, is_admin: bool = False) -> UserAccount:
        normalized_username = username.strip().lower()
        existing = self.get_user_by_username(normalized_username)
        if existing is not None:
            raise LicenseError("Username already exists.")
        password_hash, password_salt = hash_password(password)
        record = UserAccount(
            username=normalized_username,
            password_hash=password_hash,
            password_salt=password_salt,
            is_admin=is_admin,
            is_active=True,
        )
        self.db.add(record)
        self.db.flush()
        self._log_event(event_type="user_created", actor_user_id=record.id, target_type="user", target_id=record.id)
        return record

    def ensure_admin_user(self, username: str, password: str) -> tuple[UserAccount, bool]:
        normalized_username = username.strip().lower()
        existing = self.get_user_by_username(normalized_username)
        if existing is None:
            return self.create_user(normalized_username, password, is_admin=True), True
        password_hash, password_salt = hash_password(password)
        existing.password_hash = password_hash
        existing.password_salt = password_salt
        existing.is_admin = True
        existing.is_active = True
        self._log_event(
            event_type="admin_bootstrap_updated",
            actor_user_id=existing.id,
            target_type="user",
            target_id=existing.id,
            details={"username": normalized_username},
        )
        return existing, False

    def issue_license(
        self,
        user: UserAccount,
        *,
        plan_name: str,
        days: int,
        max_devices: int,
        max_concurrent_sessions: int,
        notes: str | None = None,
    ) -> LicenseRecord:
        record = LicenseRecord(
            user_id=user.id,
            license_code=create_license_code(),
            status="active",
            plan_name=plan_name,
            expires_at=utcnow() + timedelta(days=max(1, days)),
            max_devices=max(1, max_devices),
            max_concurrent_sessions=max(1, max_concurrent_sessions),
            notes=notes,
        )
        self.db.add(record)
        self.db.flush()
        self._log_event(
            event_type="license_issued",
            actor_user_id=user.id,
            target_type="license",
            target_id=record.id,
            details={"plan_name": plan_name, "days": days, "max_devices": max_devices, "max_concurrent_sessions": max_concurrent_sessions},
        )
        return record

    def get_user(self, user_id: str) -> UserAccount:
        record = self.db.get(UserAccount, user_id)
        if record is None:
            raise LicenseError("User not found.")
        return record

    def get_license(self, license_id: str) -> LicenseRecord:
        record = self.db.get(LicenseRecord, license_id)
        if record is None:
            raise LicenseError("License not found.")
        return record

    def get_device(self, device_id: str) -> DeviceRecord:
        record = self.db.get(DeviceRecord, device_id)
        if record is None:
            raise LicenseError("Device not found.")
        return record

    def get_session(self, session_id: str) -> LoginSessionRecord:
        record = self.db.get(LoginSessionRecord, session_id)
        if record is None:
            raise LicenseError("Session not found.")
        return record

    def get_current_license(self, user: UserAccount) -> LicenseRecord:
        statement = (
            select(LicenseRecord)
            .where(LicenseRecord.user_id == user.id)
            .order_by(LicenseRecord.expires_at.desc(), LicenseRecord.created_at.desc())
        )
        record = self.db.execute(statement).scalar_one_or_none()
        if record is None:
            raise LicenseError("No license has been issued for this account.")
        if not user.is_active:
            raise LicenseError("This account is disabled.")
        if record.status != "active":
            raise LicenseError("This license is not active.")
        if _as_utc(record.expires_at) <= utcnow():
            raise LicenseError("This license has expired.")
        return record

    def bind_device(self, *, user: UserAccount, license_record: LicenseRecord, device_fingerprint: str, device_label: str, platform_name: str, app_version: str | None) -> DeviceRecord:
        statement = select(DeviceRecord).where(
            DeviceRecord.user_id == user.id,
            DeviceRecord.device_fingerprint == device_fingerprint,
        )
        record = self.db.execute(statement).scalar_one_or_none()
        if record is not None:
            if record.revoked_at is not None or not record.is_active:
                raise LicenseError("This device has been revoked by the admin.")
            record.device_label = device_label
            record.platform_name = platform_name
            record.app_version = app_version
            record.last_seen_at = utcnow()
            return record

        active_device_count = self.db.execute(
            select(func.count(DeviceRecord.id)).where(
                DeviceRecord.user_id == user.id,
                DeviceRecord.revoked_at.is_(None),
                DeviceRecord.is_active.is_(True),
            )
        ).scalar_one()
        if int(active_device_count or 0) >= license_record.max_devices:
            raise LicenseError("This account has reached its device limit.")

        record = DeviceRecord(
            user_id=user.id,
            device_fingerprint=device_fingerprint,
            device_label=device_label,
            platform_name=platform_name,
            app_version=app_version,
            last_seen_at=utcnow(),
        )
        self.db.add(record)
        self.db.flush()
        self._log_event(
            event_type="device_bound",
            actor_user_id=user.id,
            target_type="device",
            target_id=record.id,
            details={"device_label": device_label, "platform_name": platform_name},
        )
        return record

    def _log_event(self, *, event_type: str, actor_user_id: str | None = None, target_type: str | None = None, target_id: str | None = None, details: dict | None = None) -> None:
        self.db.add(
            AuditLogRecord(
                actor_user_id=actor_user_id,
                event_type=event_type,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )
        )


class AdminService:
    def __init__(self, db: Session, config: LicenseServerConfig):
        self.db = db
        self.config = config
        self.license_service = LicenseService(db, config)

    def list_users(self) -> list[UserAccount]:
        return self.db.execute(select(UserAccount).order_by(UserAccount.created_at.desc())).scalars().all()

    def list_licenses(self) -> list[LicenseRecord]:
        return self.db.execute(select(LicenseRecord).order_by(LicenseRecord.created_at.desc())).scalars().all()

    def list_devices(self, user_id: str) -> list[DeviceRecord]:
        return self.db.execute(
            select(DeviceRecord)
            .where(DeviceRecord.user_id == user_id)
            .order_by(DeviceRecord.last_seen_at.desc())
        ).scalars().all()

    def list_sessions(self, user_id: str) -> list[LoginSessionRecord]:
        return self.db.execute(
            select(LoginSessionRecord)
            .where(LoginSessionRecord.user_id == user_id)
            .order_by(LoginSessionRecord.last_seen_at.desc())
        ).scalars().all()

    def create_user(self, *, username: str, password: str, is_admin: bool) -> UserAccount:
        return self.license_service.create_user(username, password, is_admin=is_admin)

    def authenticate_admin(self, *, username: str, password: str) -> UserAccount:
        user = self.license_service.get_user_by_username(username)
        if user is None or not verify_password(password, user.password_hash, user.password_salt):
            raise AuthError("Incorrect username or password.")
        if not user.is_active:
            raise AuthError("This admin account is disabled.")
        if not user.is_admin:
            raise AuthError("This account does not have admin access.")
        return user

    def issue_license(
        self,
        *,
        user_id: str,
        plan_name: str,
        days: int,
        max_devices: int,
        max_concurrent_sessions: int,
        notes: str | None,
    ) -> LicenseRecord:
        user = self.license_service.get_user(user_id)
        return self.license_service.issue_license(
            user,
            plan_name=plan_name,
            days=days,
            max_devices=max_devices,
            max_concurrent_sessions=max_concurrent_sessions,
            notes=notes,
        )

    def extend_license(self, *, license_id: str, extra_days: int) -> LicenseRecord:
        record = self.license_service.get_license(license_id)
        baseline = _as_utc(record.expires_at) if _as_utc(record.expires_at) > utcnow() else utcnow()
        record.expires_at = baseline + timedelta(days=max(1, extra_days))
        self.license_service._log_event(
            event_type="license_extended",
            actor_user_id=record.user_id,
            target_type="license",
            target_id=record.id,
            details={"extra_days": extra_days, "new_expires_at": record.expires_at.isoformat()},
        )
        return record

    def set_license_status(self, *, license_id: str, status: str) -> LicenseRecord:
        record = self.license_service.get_license(license_id)
        record.status = status
        self.license_service._log_event(
            event_type="license_status_changed",
            actor_user_id=record.user_id,
            target_type="license",
            target_id=record.id,
            details={"status": status},
        )
        if status != "active":
            self.revoke_sessions_for_license(license_id, reason="license_%s" % status)
        return record

    def set_user_status(self, *, user_id: str, is_active: bool) -> UserAccount:
        record = self.license_service.get_user(user_id)
        record.is_active = is_active
        self.license_service._log_event(
            event_type="user_status_changed",
            actor_user_id=record.id,
            target_type="user",
            target_id=record.id,
            details={"is_active": is_active},
        )
        if not is_active:
            self.revoke_sessions_for_user(user_id, reason="user_disabled")
        return record

    def revoke_device(self, *, device_id: str, reason: str | None) -> DeviceRecord:
        record = self.license_service.get_device(device_id)
        record.is_active = False
        record.revoked_at = utcnow()
        self.license_service._log_event(
            event_type="device_revoked",
            actor_user_id=record.user_id,
            target_type="device",
            target_id=record.id,
            details={"reason": reason or "admin_revoked"},
        )
        for session in self.db.execute(
            select(LoginSessionRecord).where(
                LoginSessionRecord.device_id == device_id,
                LoginSessionRecord.revoked_at.is_(None),
            )
        ).scalars():
            session.revoked_at = utcnow()
            session.revoke_reason = reason or "device_revoked"
        return record

    def revoke_session(self, *, session_id: str, reason: str | None) -> LoginSessionRecord:
        record = self.license_service.get_session(session_id)
        record.revoked_at = utcnow()
        record.revoke_reason = reason or "admin_revoked"
        self.license_service._log_event(
            event_type="session_revoked",
            actor_user_id=record.user_id,
            target_type="session",
            target_id=record.id,
            details={"reason": record.revoke_reason},
        )
        return record

    def revoke_sessions_for_license(self, license_id: str, *, reason: str) -> None:
        for session in self.db.execute(
            select(LoginSessionRecord).where(
                LoginSessionRecord.license_id == license_id,
                LoginSessionRecord.revoked_at.is_(None),
            )
        ).scalars():
            session.revoked_at = utcnow()
            session.revoke_reason = reason

    def revoke_sessions_for_user(self, user_id: str, *, reason: str) -> None:
        for session in self.db.execute(
            select(LoginSessionRecord).where(
                LoginSessionRecord.user_id == user_id,
                LoginSessionRecord.revoked_at.is_(None),
            )
        ).scalars():
            session.revoked_at = utcnow()
            session.revoke_reason = reason


class AuthService:
    def __init__(self, db: Session, config: LicenseServerConfig, signer: TokenSigner):
        self.db = db
        self.config = config
        self.signer = signer
        self.license_service = LicenseService(db, config)

    def login(self, *, username: str, password: str, device_fingerprint: str, device_label: str, platform_name: str, app_version: str | None) -> AuthBundle:
        user = self.license_service.get_user_by_username(username)
        if user is None or not verify_password(password, user.password_hash, user.password_salt):
            raise AuthError("Incorrect username or password.")
        license_record = self.license_service.get_current_license(user)
        device = self.license_service.bind_device(
            user=user,
            license_record=license_record,
            device_fingerprint=device_fingerprint,
            device_label=device_label,
            platform_name=platform_name,
            app_version=app_version,
        )
        self._cleanup_stale_sessions(license_record.id)
        self._revoke_device_sessions(device.id, reason="relogin")
        self._enforce_session_limit(license_record.id, license_record.max_concurrent_sessions)
        return self._issue_bundle(user=user, license_record=license_record, device=device)

    def refresh(self, *, refresh_token: str, device_fingerprint: str) -> AuthBundle:
        session = self.db.execute(
            select(LoginSessionRecord).where(
                LoginSessionRecord.refresh_token_hash == hash_refresh_token(refresh_token),
                LoginSessionRecord.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if session is None:
            raise AuthError("Refresh token is invalid.")
        if _as_utc(session.refresh_expires_at) <= utcnow():
            raise AuthError("Refresh token has expired.")
        user = session.user
        license_record = session.license
        device = session.device
        if device.device_fingerprint != device_fingerprint:
            raise AuthError("This refresh token belongs to a different device.")
        self.license_service.get_current_license(user)
        self._cleanup_stale_sessions(license_record.id)
        session.revoked_at = utcnow()
        session.revoke_reason = "refresh_rotated"
        return self._issue_bundle(user=user, license_record=license_record, device=device)

    def heartbeat(self, *, session_id: str) -> None:
        session = self.db.get(LoginSessionRecord, session_id)
        if session is None or session.revoked_at is not None:
            raise AuthError("Session not found.")
        if _as_utc(session.refresh_expires_at) <= utcnow():
            raise AuthError("Session expired.")
        self.license_service.get_current_license(session.user)
        if session.device.revoked_at is not None or not session.device.is_active:
            raise LicenseError("This device has been revoked by the admin.")
        session.last_seen_at = utcnow()
        session.device.last_seen_at = utcnow()

    def logout(self, *, session_id: str) -> None:
        session = self.db.get(LoginSessionRecord, session_id)
        if session is None or session.revoked_at is not None:
            return
        session.revoked_at = utcnow()
        session.revoke_reason = "logout"

    def snapshot_for_token(self, token_payload: dict) -> dict:
        session = self.db.get(LoginSessionRecord, token_payload["sid"])
        if session is None or session.revoked_at is not None:
            raise AuthError("Session is no longer active.")
        self.license_service.get_current_license(session.user)
        return {
            "account_id": session.user.id,
            "username": session.user.username,
            "license_id": session.license.id,
            "license_code": session.license.license_code,
            "plan_name": session.license.plan_name,
            "license_expires_at": session.license.expires_at,
            "device_id": session.device.id,
            "device_label": session.device.device_label,
        }

    def _issue_bundle(self, *, user: UserAccount, license_record: LicenseRecord, device: DeviceRecord) -> AuthBundle:
        refresh_token = generate_refresh_token()
        refresh_expires_at = utcnow() + timedelta(hours=self.config.refresh_token_ttl_hours)
        session = LoginSessionRecord(
            user_id=user.id,
            license_id=license_record.id,
            device_id=device.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            access_token_jti="pending",
            refresh_expires_at=refresh_expires_at,
            last_seen_at=utcnow(),
        )
        self.db.add(session)
        self.db.flush()
        access_token, access_expires_at, jti = self.signer.issue_access_token(
            account_id=user.id,
            username=user.username,
            license_id=license_record.id,
            session_id=session.id,
            device_id=device.id,
            plan_name=license_record.plan_name,
            expires_delta=timedelta(minutes=self.config.access_token_ttl_minutes),
        )
        session.access_token_jti = jti
        device.last_seen_at = utcnow()
        return AuthBundle(
            access_token=access_token,
            access_token_expires_at=access_expires_at,
            refresh_token=refresh_token,
            refresh_token_expires_at=refresh_expires_at,
            session_id=session.id,
            account_id=user.id,
            username=user.username,
            license_id=license_record.id,
            license_code=license_record.license_code,
            plan_name=license_record.plan_name,
            license_expires_at=license_record.expires_at,
            device_id=device.id,
            device_fingerprint=device.device_fingerprint,
        )

    def _cleanup_stale_sessions(self, license_id: str) -> None:
        now = utcnow()
        stale_before = now - timedelta(minutes=self.config.session_stale_minutes)
        sessions = self.db.execute(
            select(LoginSessionRecord).where(
                LoginSessionRecord.license_id == license_id,
                LoginSessionRecord.revoked_at.is_(None),
            )
        ).scalars().all()
        for session in sessions:
            if _as_utc(session.refresh_expires_at) <= now:
                session.revoked_at = now
                session.revoke_reason = "refresh_expired"
            elif _as_utc(session.last_seen_at) < stale_before:
                session.revoked_at = now
                session.revoke_reason = "stale_heartbeat"

    def _revoke_device_sessions(self, device_id: str, *, reason: str) -> None:
        statement = select(LoginSessionRecord).where(
            LoginSessionRecord.device_id == device_id,
            LoginSessionRecord.revoked_at.is_(None),
        )
        for session in self.db.execute(statement).scalars():
            session.revoked_at = utcnow()
            session.revoke_reason = reason

    def _enforce_session_limit(self, license_id: str, max_concurrent_sessions: int) -> None:
        stale_before = utcnow() - timedelta(minutes=self.config.session_stale_minutes)
        active_sessions = self.db.execute(_active_session_query(license_id, stale_before)).scalars().all()
        if len(active_sessions) >= max(1, max_concurrent_sessions):
            raise LicenseError("This account is already active on the maximum number of sessions.")
