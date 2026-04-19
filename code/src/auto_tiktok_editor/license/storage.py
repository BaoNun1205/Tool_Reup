from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import base64
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
from typing import Any, Optional

from auto_tiktok_editor.license.models import LicenseTokenBundle


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _build_blob(value: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob()
    blob.cbData = len(value)
    blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    blob._buffer = buffer  # type: ignore[attr-defined]
    return blob


def _dpapi_protect(value: bytes) -> bytes:
    if not value:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source_blob = _build_blob(value)
    target_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source_blob),
        "AutoTikTokEditorLicenseCache",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target_blob),
    ):
        raise OSError("Windows DPAPI could not encrypt the local license cache.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _dpapi_unprotect(value: bytes) -> bytes:
    if not value:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source_blob = _build_blob(value)
    target_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target_blob),
    ):
        raise OSError("Windows DPAPI could not decrypt the local license cache.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def _bundle_from_payload(payload: dict[str, Any]) -> LicenseTokenBundle:
    last_verified_at = payload.get("last_verified_at") or payload["cached_at"]
    offline_grace_expires_at = payload.get("offline_grace_expires_at") or payload["access_token_expires_at"]
    return LicenseTokenBundle(
        access_token=payload["access_token"],
        access_token_expires_at=_parse_datetime(payload["access_token_expires_at"]),
        refresh_token=payload["refresh_token"],
        refresh_token_expires_at=_parse_datetime(payload["refresh_token_expires_at"]),
        session_id=payload["session_id"],
        account_id=payload["account_id"],
        username=payload["username"],
        license_id=payload["license_id"],
        license_code=payload["license_code"],
        plan_name=payload["plan_name"],
        license_expires_at=_parse_datetime(payload["license_expires_at"]),
        device_id=payload["device_id"],
        device_fingerprint=payload["device_fingerprint"],
        public_key_b64=payload["public_key_b64"],
        server_base_url=payload["server_base_url"],
        cached_at=_parse_datetime(payload["cached_at"]),
        server_time=_parse_datetime(payload["server_time"]),
        last_verified_at=_parse_datetime(last_verified_at),
        offline_grace_expires_at=_parse_datetime(offline_grace_expires_at),
    )


class LicenseCacheStore:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.fallback_cache_path = Path.home() / ".auto_tiktok_editor" / cache_path.name

    def _candidate_paths(self) -> list[Path]:
        candidates = [self.cache_path]
        if self.fallback_cache_path != self.cache_path:
            candidates.append(self.fallback_cache_path)
        return candidates

    def load(self) -> Optional[LicenseTokenBundle]:
        for path in self._candidate_paths():
            if not path.exists():
                continue
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(envelope, dict) and envelope.get("format") == "dpapi-v1":
                    protected_payload = base64.b64decode(str(envelope["payload"]).encode("ascii"))
                    payload = json.loads(_dpapi_unprotect(protected_payload).decode("utf-8"))
                    return _bundle_from_payload(payload)
                if isinstance(envelope, dict):
                    return _bundle_from_payload(envelope)
                raise ValueError("License cache format is invalid.")
            except PermissionError:
                continue
            except (OSError, ValueError, json.JSONDecodeError):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
        return None

    def save(self, bundle: LicenseTokenBundle) -> None:
        payload: dict[str, Any] = asdict(bundle)
        for key in (
            "access_token_expires_at",
            "refresh_token_expires_at",
            "license_expires_at",
            "cached_at",
            "server_time",
            "last_verified_at",
            "offline_grace_expires_at",
        ):
            payload[key] = _serialize_datetime(payload[key])
        protected_payload = _dpapi_protect(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        envelope = {
            "format": "dpapi-v1",
            "payload": base64.b64encode(protected_payload).decode("ascii"),
        }
        body = json.dumps(envelope, indent=2)
        last_error: Exception | None = None
        for path in self._candidate_paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
                return
            except PermissionError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error

    def clear(self) -> None:
        for path in self._candidate_paths():
            try:
                if path.exists():
                    path.unlink()
            except PermissionError:
                continue
