from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any

from license_server.app.config import LicenseServerConfig

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import SigningKey, VerifyKey
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    BadSignatureError = Exception
    SigningKey = None  # type: ignore[assignment]
    VerifyKey = None  # type: ignore[assignment]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    material = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), material, 120_000)
    return base64.b64encode(derived).decode("ascii"), base64.b64encode(material).decode("ascii")


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    salt = base64.b64decode(stored_salt.encode("ascii"))
    candidate, _ = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, stored_hash)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_license_code() -> str:
    return secrets.token_hex(8).upper()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _session_secret_bytes(config: LicenseServerConfig) -> bytes:
    return config.load_admin_session_secret().encode("utf-8")


def sign_admin_session(
    config: LicenseServerConfig,
    *,
    user_id: str,
    username: str,
    is_admin: bool,
    expires_at: datetime,
) -> str:
    payload = {
        "sub": user_id,
        "usr": username,
        "adm": bool(is_admin),
        "exp": int(expires_at.timestamp()),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret_bytes(config), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return "%s.%s" % (payload_b64, _b64url_encode(signature))


def verify_admin_session(config: LicenseServerConfig, token: str) -> dict[str, Any]:
    try:
        payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise ValueError("Malformed admin session token.") from exc
    expected_signature = hmac.new(_session_secret_bytes(config), payload_b64.encode("ascii"), hashlib.sha256).digest()
    actual_signature = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise ValueError("Invalid admin session signature.")
    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if int(payload.get("exp", 0)) <= int(utcnow().timestamp()):
        raise ValueError("Admin session expired.")
    return payload


class TokenSigner:
    def __init__(self, config: LicenseServerConfig):
        if SigningKey is None:
            raise RuntimeError("PyNaCl is required to issue or verify license tokens.")
        seed = config.load_signing_seed()
        if seed:
            self._signing_key = SigningKey(seed)
        else:
            self._signing_key = SigningKey.generate()
            config.persist_signing_seed(bytes(self._signing_key))
        self._verify_key = self._signing_key.verify_key

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self._verify_key)).decode("ascii")

    def issue_access_token(
        self,
        *,
        account_id: str,
        username: str,
        license_id: str,
        session_id: str,
        device_id: str,
        plan_name: str,
        expires_delta: timedelta,
    ) -> tuple[str, datetime, str]:
        issued_at = utcnow()
        expires_at = issued_at + expires_delta
        jti = uuid.uuid4().hex
        payload = {
            "typ": "access",
            "sub": account_id,
            "usr": username,
            "lic": license_id,
            "sid": session_id,
            "did": device_id,
            "plan": plan_name,
            "jti": jti,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        header = {"alg": "EdDSA", "typ": "JWT"}
        signing_input = "%s.%s" % (
            _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")),
        )
        signature = self._signing_key.sign(signing_input.encode("utf-8")).signature
        token = "%s.%s" % (signing_input, _b64url_encode(signature))
        return token, expires_at, jti

    def verify_access_token(self, token: str) -> dict[str, Any]:
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
        except ValueError as exc:
            raise ValueError("Malformed token.") from exc
        signing_input = "%s.%s" % (header_b64, payload_b64)
        signature = _b64url_decode(signature_b64)
        try:
            self._verify_key.verify(signing_input.encode("utf-8"), signature)
        except BadSignatureError as exc:
            raise ValueError("Invalid access token signature.") from exc
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) <= int(utcnow().timestamp()):
            raise ValueError("Access token expired.")
        return payload


def load_verify_key(public_key_b64: str) -> VerifyKey:
    if VerifyKey is None:
        raise RuntimeError("PyNaCl is required to verify license tokens.")
    return VerifyKey(base64.b64decode(public_key_b64.encode("ascii")))
