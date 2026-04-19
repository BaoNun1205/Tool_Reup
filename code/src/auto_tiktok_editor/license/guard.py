from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from auto_tiktok_editor.license.client import LicenseApiClient
from auto_tiktok_editor.license.config import LicenseClientConfig
from auto_tiktok_editor.license.exceptions import LicenseAuthenticationRequired, LicenseError, LicenseServerUnavailable, LicenseVerificationError
from auto_tiktok_editor.license.machine import build_device_fingerprint, build_device_label
from auto_tiktok_editor.license.models import DeviceIdentity, LicenseTokenBundle, VerifiedLicenseSession
from auto_tiktok_editor.license.storage import LicenseCacheStore
from auto_tiktok_editor.license.tokens import verify_access_token


class LicenseGuard:
    def __init__(self, config: LicenseClientConfig | None = None):
        self.config = config or LicenseClientConfig.from_env()
        self.client = LicenseApiClient(self.config)
        self.store = LicenseCacheStore(self.config.cache_path)

    def current_device(self, app_version: str | None = None) -> DeviceIdentity:
        return DeviceIdentity(
            fingerprint=build_device_fingerprint(),
            label=build_device_label(),
            platform_name="windows",
            app_version=app_version,
        )

    def login(self, *, username: str, password: str, app_version: str | None = None) -> VerifiedLicenseSession:
        bundle = self.client.login(username=username, password=password, device=self.current_device(app_version))
        bundle = self._mark_verified(bundle)
        session = self._verify_bundle(bundle)
        self.store.save(bundle)
        return session

    def ensure_valid_session(self) -> VerifiedLicenseSession:
        bundle = self.store.load()
        if bundle is None:
            raise LicenseAuthenticationRequired("No cached license session was found.")
        if bundle.device_fingerprint != build_device_fingerprint():
            self.store.clear()
            raise LicenseAuthenticationRequired("Cached license belongs to a different device.")
        try:
            if self._should_refresh(bundle):
                return self._refresh(bundle)
            return self._verify_bundle(bundle)
        except LicenseServerUnavailable:
            return self._verify_bundle(bundle, allow_offline_grace=True)
        except LicenseError as exc:
            if self._should_clear_cached_session(exc):
                self.store.clear()
                raise LicenseAuthenticationRequired("Phiên đăng nhập hiện tại không còn hợp lệ. Hãy đăng nhập lại.") from exc
            raise

    def ensure_online_session(self) -> VerifiedLicenseSession:
        bundle = self.store.load()
        if bundle is None:
            raise LicenseAuthenticationRequired("No cached license session was found.")
        if bundle.device_fingerprint != build_device_fingerprint():
            self.store.clear()
            raise LicenseAuthenticationRequired("Cached license belongs to a different device.")
        try:
            if self._should_refresh(bundle):
                return self._refresh(bundle)
            session = self._verify_bundle(bundle)
            self.client.me(access_token=bundle.access_token)
            self.client.heartbeat(access_token=bundle.access_token, session_id=session.session_id)
            self.store.save(self._mark_verified(bundle))
            return session
        except LicenseServerUnavailable:
            raise
        except LicenseError as exc:
            if self._should_clear_cached_session(exc):
                self.store.clear()
                raise LicenseAuthenticationRequired("Phiên đăng nhập hiện tại không còn hợp lệ. Hãy đăng nhập lại.") from exc
            raise

    def heartbeat(self) -> VerifiedLicenseSession:
        session = self.ensure_valid_session()
        bundle = self.store.load()
        if bundle is None:
            raise LicenseAuthenticationRequired("No cached session was found.")
        try:
            self.client.heartbeat(access_token=bundle.access_token, session_id=session.session_id)
            self.store.save(self._mark_verified(bundle))
        except LicenseServerUnavailable:
            return session
        except LicenseError as exc:
            if self._should_clear_cached_session(exc):
                self.store.clear()
                raise LicenseAuthenticationRequired("Phiên đăng nhập hiện tại không còn hợp lệ. Hãy đăng nhập lại.") from exc
            raise
        return session

    def logout(self) -> None:
        bundle = self.store.load()
        if bundle is None:
            return
        try:
            self.client.logout(access_token=bundle.access_token)
        finally:
            self.store.clear()

    def _refresh(self, bundle: LicenseTokenBundle) -> VerifiedLicenseSession:
        refreshed = self.client.refresh(
            refresh_token=bundle.refresh_token,
            device_fingerprint=bundle.device_fingerprint,
        )
        refreshed = self._mark_verified(refreshed)
        session = self._verify_bundle(refreshed)
        self.store.save(refreshed)
        return session

    def _verify_bundle(self, bundle: LicenseTokenBundle, *, allow_offline_grace: bool = False) -> VerifiedLicenseSession:
        allow_expired = allow_offline_grace and self._is_offline_grace_active(bundle)
        session = verify_access_token(bundle.access_token, bundle.public_key_b64, allow_expired=allow_expired)
        if session.device_id != bundle.device_id:
            raise LicenseVerificationError("Access token does not match the cached device binding.")
        if allow_offline_grace:
            self._ensure_bundle_is_still_usable_offline(bundle)
        return replace(session, license_expires_at=bundle.license_expires_at)

    def _should_refresh(self, bundle: LicenseTokenBundle) -> bool:
        refresh_deadline = bundle.access_token_expires_at - timedelta(seconds=self.config.refresh_leeway_seconds)
        return refresh_deadline <= datetime.now(timezone.utc)

    def _mark_verified(self, bundle: LicenseTokenBundle) -> LicenseTokenBundle:
        now = datetime.now(timezone.utc)
        return replace(
            bundle,
            cached_at=now,
            last_verified_at=now,
            offline_grace_expires_at=now + timedelta(hours=self.config.offline_grace_hours),
        )

    def _is_offline_grace_active(self, bundle: LicenseTokenBundle) -> bool:
        now = datetime.now(timezone.utc)
        offline_deadline = min(bundle.offline_grace_expires_at, bundle.license_expires_at)
        return offline_deadline > now

    def _ensure_bundle_is_still_usable_offline(self, bundle: LicenseTokenBundle) -> None:
        if not self._is_offline_grace_active(bundle):
            raise LicenseVerificationError("Offline grace period has ended. Please reconnect to verify your license.")

    def _should_clear_cached_session(self, exc: LicenseError) -> bool:
        message = str(exc).lower()
        return (
            "đăng nhập lại" in message
            or "không còn hợp lệ" in message
            or "hết hạn" in message
            or "đã bị khóa" in message
        )
