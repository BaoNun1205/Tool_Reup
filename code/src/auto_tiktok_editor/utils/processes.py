"""Owned subprocess lifecycle helpers.

The GUI starts several process trees (Telegram, Playwright/Chrome, scrcpy,
ffmpeg, and small PowerShell helpers).  On Windows, terminating only the
immediate ``Popen`` PID does not terminate its descendants, so shutdown must
explicitly address the whole tree.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess
from typing import Mapping


_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def terminate_process_tree(process: subprocess.Popen | None, *, timeout: float = 3.0) -> bool:
    """Terminate a tracked process and every descendant owned by it."""
    if process is None:
        return True
    try:
        if process.poll() is not None:
            return True
    except Exception:
        pass

    pid = int(getattr(process, "pid", 0) or 0)
    if os.name == "nt" and pid > 0 and _taskkill_tree(pid, timeout=timeout):
        try:
            process.wait(timeout=timeout)
        except Exception:
            pass
        return True

    try:
        process.terminate()
        process.wait(timeout=timeout)
        return True
    except Exception:
        try:
            process.kill()
            process.wait(timeout=max(1.0, timeout))
            return True
        except Exception:
            return False


def terminate_child_process_trees(parent_pid: int | None = None, *, timeout: float = 3.0) -> int:
    """Terminate every currently-owned child tree of ``parent_pid`` on Windows.

    This is the final shutdown safety net for children created indirectly by
    libraries such as Playwright or by a worker currently blocked in ffmpeg.
    """
    if os.name != "nt":
        return 0
    owner_pid = int(parent_pid or os.getpid())
    parent_map = _windows_process_parent_map()
    roots = _direct_child_roots(owner_pid, parent_map)
    stopped = 0
    for pid in roots:
        if _taskkill_tree(pid, timeout=timeout):
            stopped += 1
    return stopped


def _direct_child_roots(parent_pid: int, parent_map: Mapping[int, int]) -> list[int]:
    """Return top-level children; ``taskkill /T`` handles their descendants."""
    return sorted(pid for pid, owner in parent_map.items() if owner == parent_pid and pid != parent_pid)


def _taskkill_tree(pid: int, *, timeout: float) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1.0, timeout),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception:
        return False


def _windows_process_parent_map() -> dict[int, int]:
    if os.name != "nt":
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, _INVALID_HANDLE_VALUE):
        return {}
    result: dict[int, int] = {}
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not process_first(snapshot, ctypes.byref(entry)):
            return result
        while True:
            result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)
    return result
