from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from typing import Any

from auto_tiktok_editor.license.exceptions import LicenseVerificationError
from auto_tiktok_editor.license.models import VerifiedLicenseSession

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    BadSignatureError = Exception
    VerifyKey = None  # type: ignore[assignment]


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def verify_access_token(token: str, public_key_b64: str, *, allow_expired: bool = False) -> VerifiedLicenseSession:
    if VerifyKey is None:
        raise LicenseVerificationError("PyNaCl is required to verify signed access tokens.")
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise LicenseVerificationError("Malformed access token.") from exc
    signing_input = "%s.%s" % (header_b64, payload_b64)
    signature = _b64url_decode(signature_b64)
    verify_key = VerifyKey(base64.b64decode(public_key_b64.encode("ascii")))
    try:
        verify_key.verify(signing_input.encode("utf-8"), signature)
    except BadSignatureError as exc:
        raise LicenseVerificationError("Access token signature is invalid.") from exc
    payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    expires_at = datetime.fromtimestamp(int(payload.get("exp", 0)), tz=timezone.utc)
    if not allow_expired and expires_at <= datetime.now(timezone.utc):
        raise LicenseVerificationError("Access token has expired.")
    return VerifiedLicenseSession(
        account_id=str(payload["sub"]),
        username=str(payload["usr"]),
        license_id=str(payload["lic"]),
        session_id=str(payload["sid"]),
        device_id=str(payload["did"]),
        plan_name=str(payload["plan"]),
        license_expires_at=expires_at,
        access_token_expires_at=expires_at,
        raw_payload=payload,
    )
