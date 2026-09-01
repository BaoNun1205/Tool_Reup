"""Cross-process single-instance guard used by Telegram polling runtimes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import time
from typing import BinaryIO


_ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    def __init__(self, name: str, *, lock_path: Path | None = None) -> None:
        self.name = str(name)
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self._handle: int | None = None
        self._file: BinaryIO | None = None

    def acquire(self) -> bool:
        if self._handle is not None or self._file is not None:
            return True
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_mutex = kernel32.CreateMutexW
            create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            create_mutex.restype = ctypes.c_void_p
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = create_mutex(None, False, self.name)
            if not handle:
                raise OSError(ctypes.get_last_error(), "Could not create the runtime mutex.")
            if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                close_handle(wintypes.HANDLE(handle))
                return False
            self._handle = int(handle)
            return True

        if self.lock_path is None:
            raise RuntimeError("A lock path is required outside Windows.")
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            lock_file.close()
            return False
        self._file = lock_file
        return True

    def release(self) -> None:
        if self._handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(self._handle))
            self._handle = None
        if self._file is not None:
            try:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> "SingleInstanceGuard":
        if not self.acquire():
            raise RuntimeError("Another instance is already running.")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def telegram_runtime_guard(project_root: Path) -> SingleInstanceGuard:
    root = Path(project_root).expanduser().resolve()
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
    return SingleInstanceGuard(
        "Local\\AutoTikTokEditorTelegramRuntime-%s" % digest,
        lock_path=root / "logs" / "telegram_runtime.lock",
    )


def profile_manager_runtime_guard(project_root: Path) -> SingleInstanceGuard:
    root = Path(project_root).expanduser().resolve()
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
    return SingleInstanceGuard(
        "Local\\AutoTikTokEditorProfileManager-%s" % digest,
        lock_path=root / "logs" / "profile_manager_runtime.lock",
    )


def activate_existing_profile_manager_window(*, wait_seconds: float = 2.0) -> bool:
    """Bring an already-loading or ready Profile Manager window to the front."""
    if os.name != "nt":
        return False
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        hwnd = _find_profile_manager_window()
        if hwnd:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _is_profile_manager_title(title: str) -> bool:
    clean = str(title or "").strip()
    return clean in {
        "TikTok Profile Manager Pro",
        "TikTok Profile Manager - Đang khởi động",
    }


def _find_profile_manager_window() -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )
    found = ctypes.c_void_p()

    def _visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if _is_profile_manager_title(buffer.value):
            found.value = int(hwnd)
            return False
        return True

    callback = enum_callback_type(_visit)
    user32.EnumWindows(callback, 0)
    return int(found.value or 0)
