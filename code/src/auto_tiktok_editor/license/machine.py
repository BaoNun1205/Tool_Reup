from __future__ import annotations

import hashlib
import os
import platform
import socket
from typing import Optional


def _windows_machine_guid() -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip()
    except OSError:
        return None


def build_device_fingerprint() -> str:
    parts = [
        _windows_machine_guid() or "",
        socket.gethostname(),
        platform.machine(),
        platform.version(),
        os.getenv("PROCESSOR_IDENTIFIER", ""),
    ]
    raw = "|".join(part for part in parts if part)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_device_label() -> str:
    return "%s (%s)" % (socket.gethostname(), platform.machine() or "unknown")
