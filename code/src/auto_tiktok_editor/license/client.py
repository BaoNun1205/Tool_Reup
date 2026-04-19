from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from urllib import error, request

from auto_tiktok_editor.license.config import LicenseClientConfig
from auto_tiktok_editor.license.exceptions import LicenseError, LicenseServerUnavailable
from auto_tiktok_editor.license.models import DeviceIdentity, LicenseTokenBundle


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _translate_server_error(detail: str) -> str:
    normalized = detail.strip()
    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            candidate = str(payload.get("detail") or "").strip()
            if candidate:
                normalized = candidate
    except json.JSONDecodeError:
        pass

    translations = {
        "No license has been issued for this account.": "Tài khoản này chưa được cấp license.",
        "This account is disabled.": "Tài khoản này đã bị khóa.",
        "This license is not active.": "License của tài khoản này hiện không hoạt động.",
        "This license has expired.": "License của tài khoản này đã hết hạn.",
        "Incorrect username or password.": "Sai tài khoản hoặc mật khẩu.",
        "This device has been revoked by the admin.": "Thiết bị này đã bị admin thu hồi quyền sử dụng.",
        "This account has reached its device limit.": "Tài khoản này đã đạt giới hạn số máy được phép dùng.",
        "This account is already active on the maximum number of sessions.": "Tài khoản này đang hoạt động ở số phiên tối đa cho phép.",
        "Refresh token is invalid.": "Phiên đăng nhập không còn hợp lệ. Hãy đăng nhập lại.",
        "Refresh token has expired.": "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
        "This refresh token belongs to a different device.": "Phiên đăng nhập này thuộc về một thiết bị khác.",
        "Session not found.": "Không tìm thấy phiên đăng nhập.",
        "Session expired.": "Phiên đăng nhập đã hết hạn.",
        "Session is no longer active.": "Phiên đăng nhập không còn hoạt động.",
        "User not found.": "Không tìm thấy tài khoản.",
        "Invalid access token signature.": "Phiên đăng nhập hiện tại không còn hợp lệ. Hãy đăng nhập lại.",
        "Access token expired.": "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
    }
    return translations.get(normalized, normalized)


class LicenseApiClient:
    def __init__(self, config: LicenseClientConfig):
        self.config = config

    def login(self, *, username: str, password: str, device: DeviceIdentity) -> LicenseTokenBundle:
        return self._request_bundle(
            "/api/v1/auth/login",
            {
                "username": username,
                "password": password,
                "device": {
                    "device_fingerprint": device.fingerprint,
                    "device_label": device.label,
                    "platform_name": device.platform_name,
                    "app_version": device.app_version,
                },
            },
        )

    def refresh(self, *, refresh_token: str, device_fingerprint: str) -> LicenseTokenBundle:
        return self._request_bundle(
            "/api/v1/auth/refresh",
            {
                "refresh_token": refresh_token,
                "device_fingerprint": device_fingerprint,
            },
        )

    def heartbeat(self, *, access_token: str, session_id: str) -> None:
        self._request_json(
            "/api/v1/auth/heartbeat",
            {"session_id": session_id},
            headers={"Authorization": "Bearer %s" % access_token},
        )

    def logout(self, *, access_token: str) -> None:
        self._request_json(
            "/api/v1/auth/logout",
            {},
            headers={"Authorization": "Bearer %s" % access_token},
        )

    def me(self, *, access_token: str) -> dict:
        url = "%s/api/v1/auth/me" % self.config.server_base_url
        req = request.Request(url, headers={"Authorization": "Bearer %s" % access_token}, method="GET")
        try:
            with request.urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LicenseError(_translate_server_error(detail)) from exc
        except error.URLError as exc:
            raise LicenseServerUnavailable("Không kết nối được tới license server.") from exc

    def _request_bundle(self, path: str, payload: dict) -> LicenseTokenBundle:
        response = self._request_json(path, payload)
        now = datetime.now(timezone.utc)
        return LicenseTokenBundle(
            access_token=response["access_token"],
            access_token_expires_at=_parse_datetime(response["access_token_expires_at"]),
            refresh_token=response["refresh_token"],
            refresh_token_expires_at=_parse_datetime(response["refresh_token_expires_at"]),
            session_id=response["session_id"],
            account_id=response["account_id"],
            username=response["username"],
            license_id=response["license_id"],
            license_code=response["license_code"],
            plan_name=response["plan_name"],
            license_expires_at=_parse_datetime(response["license_expires_at"]),
            device_id=response["device_id"],
            device_fingerprint=response["device_fingerprint"],
            public_key_b64=response["public_key_b64"],
            server_base_url=self.config.server_base_url,
            cached_at=now,
            server_time=_parse_datetime(response["server_time"]),
            last_verified_at=now,
            offline_grace_expires_at=now + timedelta(hours=self.config.offline_grace_hours),
        )

    def _request_json(self, path: str, payload: dict, headers: dict | None = None) -> dict:
        url = "%s%s" % (self.config.server_base_url, path)
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        req = request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LicenseError(_translate_server_error(detail)) from exc
        except error.URLError as exc:
            raise LicenseServerUnavailable("Không kết nối được tới license server.") from exc
