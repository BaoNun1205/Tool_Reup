"""ADB and scrcpy integration for controlling an Android phone."""

from __future__ import annotations

import ctypes
import base64
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import quote
from ctypes import wintypes
import xml.etree.ElementTree as ET

from auto_tiktok_editor.app.device_transfer import AndroidDeviceTransfer
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.phone_uiautomator import UiAutomatorClient, UiAutomatorUnavailable
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.utils.processes import terminate_process_tree


DEFAULT_ADB_PORT = 5555
DEFAULT_PUSH_TARGET = "/sdcard/DCIM/Camera/"
MEDIA_SCAN_INTERVAL_SECONDS = 4.0
SCRCPY_MAX_SIZE = 1280
SCRCPY_MAX_FPS = 60
SCRCPY_VIDEO_BIT_RATE = "6M"
SCRCPY_SHORTCUT_MOD = "rctrl"
SCRCPY_WINDOW_TITLE = "TikTok Tool - Phone Control"
SCRCPY_DOCK_WIDTH = 420
SCREENSHOT_HOTKEY_LABEL = "Ctrl+Alt+S"
CLOSE_HOTKEY_LABEL = "Ctrl+Alt+Q"
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
MAPVK_VK_TO_VSC = 0
VK_RCONTROL = 0xA3
VK_V = 0x56
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
ANDROID_KEYCODE_BACK = "4"
ANDROID_KEYCODE_DEL = "67"
ANDROID_KEYCODE_ENTER = "66"
ANDROID_KEYCODE_MOVE_END = "123"
ANDROID_KEYCODE_SPACE = "62"
DOCK_POSITIONS = {"off", "left", "right"}
MONITOR_TARGETS = {"primary", "secondary"}
CONNECTION_MODES = {"wifi", "usb"}
SCRCPY_MAX_SIZE_OPTIONS = {1024, 1280, 1600}
SCRCPY_MAX_FPS_OPTIONS = {30, 60}
SCRCPY_VIDEO_BIT_RATE_OPTIONS = {"4M", "6M", "8M"}
GALLERY_MEDIA_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".png",
    ".webm",
    ".webp",
}
GALLERY_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
TIKTOK_ANDROID_PACKAGES = (
    "com.ss.android.ugc.trill",
    "com.ss.android.ugc.aweme",
    "com.zhiliaoapp.musically",
    "com.zhiliaoapp.musically.go",
)
TIKTOK_UPLOAD_DEEPLINKS = (
    "snssdk1233://aweme/publish",
    "tiktok://upload",
)
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
ADB_KEYBOARD_INPUT_B64_ACTION = "ADB_INPUT_B64"
ADB_KEYBOARD_READY_DELAY_SECONDS = 0.7
ADB_KEYBOARD_COMMIT_DELAY_SECONDS = 0.8
ANDROID_CLIPBOARD_VERIFY_ATTEMPTS = 3
ANDROID_CLIPBOARD_VERIFY_DELAY_SECONDS = 0.25


class _AppBarData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _settings_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AutoTikTokEditor" / "phone_control_settings.json"
    return Path.home() / ".auto_tiktok_editor" / "phone_control_settings.json"


@dataclass(frozen=True)
class PhoneControlSettings:
    address: str = ""
    connection_mode: str = "wifi"
    keep_screen_awake: bool = False
    turn_screen_off: bool = False
    always_on_top: bool = False
    dock_position: str = "off"
    monitor_target: str = "primary"
    max_size: int = SCRCPY_MAX_SIZE
    max_fps: int = SCRCPY_MAX_FPS
    video_bit_rate: str = SCRCPY_VIDEO_BIT_RATE


def normalize_dock_position(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in DOCK_POSITIONS else "off"


def normalize_monitor_target(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in MONITOR_TARGETS else "primary"


def normalize_connection_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in CONNECTION_MODES else "wifi"


def normalize_scrcpy_max_size(value: object) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return SCRCPY_MAX_SIZE
    return normalized if normalized in SCRCPY_MAX_SIZE_OPTIONS else SCRCPY_MAX_SIZE


def normalize_scrcpy_max_fps(value: object) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return SCRCPY_MAX_FPS
    return normalized if normalized in SCRCPY_MAX_FPS_OPTIONS else SCRCPY_MAX_FPS


def normalize_scrcpy_video_bit_rate(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return (
        normalized
        if normalized in SCRCPY_VIDEO_BIT_RATE_OPTIONS
        else SCRCPY_VIDEO_BIT_RATE
    )


def load_phone_control_settings() -> PhoneControlSettings:
    path = _settings_path()
    if not path.exists():
        return PhoneControlSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return PhoneControlSettings()
    if not isinstance(payload, dict):
        return PhoneControlSettings()
    return PhoneControlSettings(
        address=str(payload.get("address") or "").strip(),
        connection_mode=normalize_connection_mode(payload.get("connection_mode")),
        keep_screen_awake=payload.get("keep_screen_awake") is True,
        turn_screen_off=payload.get("turn_screen_off") is True,
        always_on_top=payload.get("always_on_top") is True,
        dock_position=normalize_dock_position(payload.get("dock_position")),
        monitor_target=normalize_monitor_target(payload.get("monitor_target")),
        max_size=normalize_scrcpy_max_size(payload.get("max_size")),
        max_fps=normalize_scrcpy_max_fps(payload.get("max_fps")),
        video_bit_rate=normalize_scrcpy_video_bit_rate(
            payload.get("video_bit_rate")
        ),
    )


def save_phone_control_settings(settings: PhoneControlSettings) -> Path:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "address": settings.address,
                "connection_mode": normalize_connection_mode(settings.connection_mode),
                "keep_screen_awake": settings.keep_screen_awake,
                "turn_screen_off": settings.turn_screen_off,
                "always_on_top": settings.always_on_top,
                "dock_position": normalize_dock_position(settings.dock_position),
                "monitor_target": normalize_monitor_target(settings.monitor_target),
                "max_size": normalize_scrcpy_max_size(settings.max_size),
                "max_fps": normalize_scrcpy_max_fps(settings.max_fps),
                "video_bit_rate": normalize_scrcpy_video_bit_rate(
                    settings.video_bit_rate
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def normalize_phone_address(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Please enter the phone IP address.")

    host = text
    port = DEFAULT_ADB_PORT
    if ":" in text:
        host, separator, raw_port = text.rpartition(":")
        if not separator or not host or not raw_port:
            raise ValueError("Phone address must use the format IP or IP:PORT.")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("Phone port must be a number.") from exc

    try:
        parsed_host = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Phone IP address is not valid.") from exc
    if parsed_host.version != 4:
        raise ValueError("Only IPv4 phone addresses are currently supported.")
    if not 1 <= port <= 65535:
        raise ValueError("Phone port must be between 1 and 65535.")
    return "%s:%d" % (parsed_host, port)


class WindowsGlobalHotkey:
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(
        self,
        callback: Callable[[], None],
        *,
        virtual_key: int = 0x53,
        thread_name: str = "phone-global-hotkey",
    ) -> None:
        self.callback = callback
        self.virtual_key = virtual_key
        self.thread_name = thread_name
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._started = threading.Event()
        self._registered = False

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._registered
        self._started.clear()
        self._thread = threading.Thread(
            target=self._message_loop,
            daemon=True,
            name=self.thread_name,
        )
        self._thread.start()
        self._started.wait(timeout=2)
        return self._registered

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id,
                self.WM_QUIT,
                0,
                0,
            )
        if thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        self._thread_id = 0
        self._registered = False

    def _message_loop(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        hotkey_id = 1
        self._registered = bool(
            user32.RegisterHotKey(
                None,
                hotkey_id,
                self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                self.virtual_key,
            )
        )
        self._started.set()
        if not self._registered:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.WM_HOTKEY and message.wParam == hotkey_id:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, hotkey_id)
            self._registered = False


class WindowsScrcpyDock:
    ABM_NEW = 0x00000000
    ABM_REMOVE = 0x00000001
    ABM_QUERYPOS = 0x00000002
    ABM_SETPOS = 0x00000003
    ABE_LEFT = 0
    ABE_RIGHT = 2
    MONITOR_DEFAULTTONEAREST = 2
    MONITORINFOF_PRIMARY = 1
    SWP_SHOWWINDOW = 0x0040
    WM_APP = 0x8000

    def __init__(self) -> None:
        self._hwnd = 0
        self._registered = False
        self._lock = threading.Lock()
        self.selected_monitor = "primary"

    def attach(
        self,
        process: subprocess.Popen,
        position: str,
        *,
        monitor_target: str = "primary",
        always_on_top: bool = False,
        stop_event: threading.Event | None = None,
        timeout: float = 8.0,
    ) -> bool:
        position = normalize_dock_position(position)
        monitor_target = normalize_monitor_target(monitor_target)
        if position == "off" or os.name != "nt":
            return False

        deadline = time.monotonic() + timeout
        hwnd = 0
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            if process.poll() is not None:
                return False
            hwnd = self._find_process_window(process)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return False

        with self._lock:
            if stop_event is not None and stop_event.is_set():
                return False
            self._remove_locked()
            return self._register_and_position(
                hwnd,
                position,
                monitor_target,
                always_on_top,
            )

    def remove(self) -> None:
        if os.name != "nt":
            return
        with self._lock:
            self._remove_locked()

    @staticmethod
    def _find_process_window(process: subprocess.Popen, *, require_title: bool = True) -> int:
        process_id = int(getattr(process, "pid", 0) or 0)
        if not process_id:
            return 0
        user32 = ctypes.windll.user32
        found = ctypes.c_void_p()

        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int

        @callback_type
        def find_window(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            window_process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_process_id))
            if window_process_id.value != process_id:
                return True
            title_length = user32.GetWindowTextLengthW(hwnd)
            if title_length <= 0:
                if require_title:
                    return True
                found.value = hwnd
                return False
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == SCRCPY_WINDOW_TITLE or not require_title:
                found.value = hwnd
                return False
            return True

        user32.EnumWindows(find_window, 0)
        return int(found.value or 0)

    def _register_and_position(
        self,
        hwnd: int,
        position: str,
        monitor_target: str,
        always_on_top: bool,
    ) -> bool:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        shell32.SHAppBarMessage.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_AppBarData),
        ]
        shell32.SHAppBarMessage.restype = ctypes.c_size_t
        user32.GetMonitorInfoW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_MonitorInfo),
        ]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        monitor, monitor_info, selected_monitor = self._select_monitor(
            user32,
            monitor_target,
        )
        if not monitor or monitor_info is None:
            return False
        self.selected_monitor = selected_monitor

        dpi = 96
        try:
            shcore = ctypes.windll.shcore
            shcore.GetDpiForMonitor.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.POINTER(wintypes.UINT),
                ctypes.POINTER(wintypes.UINT),
            ]
            shcore.GetDpiForMonitor.restype = ctypes.c_long
            dpi_x = wintypes.UINT()
            dpi_y = wintypes.UINT()
            if shcore.GetDpiForMonitor(
                monitor,
                0,
                ctypes.byref(dpi_x),
                ctypes.byref(dpi_y),
            ) == 0:
                dpi = int(dpi_x.value or dpi)
        except (AttributeError, OSError):
            pass
        dock_width = max(320, round(SCRCPY_DOCK_WIDTH * dpi / 96))
        monitor_width = monitor_info.rcMonitor.right - monitor_info.rcMonitor.left
        dock_width = min(dock_width, max(320, monitor_width // 2))

        edge = self.ABE_LEFT if position == "left" else self.ABE_RIGHT
        appbar = _AppBarData()
        appbar.cbSize = ctypes.sizeof(_AppBarData)
        appbar.hWnd = hwnd
        appbar.uCallbackMessage = self.WM_APP + 31
        if not shell32.SHAppBarMessage(self.ABM_NEW, ctypes.byref(appbar)):
            return False

        appbar.uEdge = edge
        appbar.rc = monitor_info.rcMonitor
        if edge == self.ABE_LEFT:
            appbar.rc.right = appbar.rc.left + dock_width
        else:
            appbar.rc.left = appbar.rc.right - dock_width
        shell32.SHAppBarMessage(self.ABM_QUERYPOS, ctypes.byref(appbar))
        if edge == self.ABE_LEFT:
            appbar.rc.right = appbar.rc.left + dock_width
        else:
            appbar.rc.left = appbar.rc.right - dock_width
        shell32.SHAppBarMessage(self.ABM_SETPOS, ctypes.byref(appbar))

        insert_after = wintypes.HWND(-1 if always_on_top else 0)
        positioned = bool(
            user32.SetWindowPos(
                hwnd,
                insert_after,
                appbar.rc.left,
                appbar.rc.top,
                appbar.rc.right - appbar.rc.left,
                appbar.rc.bottom - appbar.rc.top,
                self.SWP_SHOWWINDOW,
            )
        )
        if not positioned:
            shell32.SHAppBarMessage(self.ABM_REMOVE, ctypes.byref(appbar))
            return False
        self._hwnd = hwnd
        self._registered = True
        return True

    def _select_monitor(
        self,
        user32,
        monitor_target: str,
    ) -> tuple[int, _MonitorInfo | None, str]:
        monitors: list[tuple[int, _MonitorInfo]] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )
        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            callback_type,
            wintypes.LPARAM,
        ]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL

        @callback_type
        def collect_monitor(monitor, _hdc, _rect, _lparam):
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                monitors.append((int(monitor), info))
            return True

        user32.EnumDisplayMonitors(None, None, collect_monitor, 0)
        if not monitors:
            return 0, None, "primary"

        primary = next(
            (
                item
                for item in monitors
                if item[1].dwFlags & self.MONITORINFOF_PRIMARY
            ),
            monitors[0],
        )
        if monitor_target == "secondary":
            secondary = next(
                (
                    item
                    for item in monitors
                    if not item[1].dwFlags & self.MONITORINFOF_PRIMARY
                ),
                None,
            )
            if secondary is not None:
                return secondary[0], secondary[1], "secondary"
        return primary[0], primary[1], "primary"

    def _remove_locked(self) -> None:
        if not self._registered or not self._hwnd:
            self._registered = False
            self._hwnd = 0
            return

        appbar = _AppBarData()
        appbar.cbSize = ctypes.sizeof(_AppBarData)
        appbar.hWnd = self._hwnd
        try:
            ctypes.windll.shell32.SHAppBarMessage(
                self.ABM_REMOVE,
                ctypes.byref(appbar),
            )
        finally:
            self._registered = False
            self._hwnd = 0


class PhoneController:
    def __init__(
        self,
        config: PipelineConfig,
        runner: CommandRunner | None = None,
        device_transfer: AndroidDeviceTransfer | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self.device_transfer = device_transfer or AndroidDeviceTransfer(config, self.runner)
        self.on_event = on_event
        self.process: subprocess.Popen | None = None
        self.connected_serial: str = ""
        self._scrcpy_monitor_thread: threading.Thread | None = None
        self._media_watcher_thread: threading.Thread | None = None
        self._media_event_process: subprocess.Popen | None = None
        self._media_watcher_stop = threading.Event()
        self._clipboard_process: subprocess.Popen | None = None
        self._clipboard_lock = threading.Lock()
        self._manual_transfer_paths: set[str] = set()
        self._manual_transfer_lock = threading.Lock()
        self._window_dock = WindowsScrcpyDock()
        self._window_dock_thread: threading.Thread | None = None
        self._window_dock_stop = threading.Event()
        self._ui_automation: UiAutomatorClient | None = None
        self._ui_automation_serial: str = ""
        self._ui_automation_unavailable = False
        self._ui_automation_error = ""
        self._previous_android_input_method = ""

    def connect(self, address: str = "", *, connection_mode: str = "wifi") -> dict[str, str]:
        mode = normalize_connection_mode(connection_mode)
        target = normalize_phone_address(address) if mode == "wifi" else ""
        self.runner.ensure_tool(self.config.adb_bin)
        selected_serial = self._connect_adb(target, connection_mode=mode)
        return {
            "address": selected_serial,
            "message": "Phone connected for file transfer: %s." % selected_serial,
        }

    def connect_and_open(
        self,
        address: str,
        *,
        keep_screen_awake: bool = False,
        turn_screen_off: bool = False,
        always_on_top: bool = False,
        dock_position: str = "off",
        monitor_target: str = "primary",
        max_size: int = SCRCPY_MAX_SIZE,
        max_fps: int = SCRCPY_MAX_FPS,
        video_bit_rate: str = SCRCPY_VIDEO_BIT_RATE,
        connection_mode: str = "wifi",
    ) -> dict[str, str]:
        mode = normalize_connection_mode(connection_mode)
        target = normalize_phone_address(address) if mode == "wifi" else ""
        if self.is_running():
            return {"address": self.connected_serial or target, "message": "Phone control is already open."}

        self.runner.ensure_tool(self.config.adb_bin)
        self.runner.ensure_tool(self.config.scrcpy_bin)
        target = self._connect_adb(target, connection_mode=mode)
        scrcpy_path = Path(self.config.scrcpy_bin)
        cwd = str(scrcpy_path.resolve().parent) if scrcpy_path.exists() else None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
        )
        command = [
            self.config.scrcpy_bin,
            "--serial",
            target,
            "--window-title",
            SCRCPY_WINDOW_TITLE,
            "--push-target",
            DEFAULT_PUSH_TARGET,
            "--max-size",
            str(normalize_scrcpy_max_size(max_size)),
            "--max-fps",
            str(normalize_scrcpy_max_fps(max_fps)),
            "--video-bit-rate",
            normalize_scrcpy_video_bit_rate(video_bit_rate),
            "--shortcut-mod=%s" % SCRCPY_SHORTCUT_MOD,
        ]
        if keep_screen_awake:
            command.append("--stay-awake")
        if turn_screen_off:
            command.append("--turn-screen-off")
        if always_on_top:
            command.append("--always-on-top")
        environment = os.environ.copy()
        environment["ADB"] = str(Path(self.config.adb_bin).expanduser().resolve())
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=creationflags,
            close_fds=False,
        )
        self._emit_event(
            "info",
            "scrcpy_started",
            "scrcpy opened for %s." % target,
            device_serial=target,
        )
        self._start_scrcpy_monitor(self.process, target)
        self._start_window_dock(
            self.process,
            normalize_dock_position(dock_position),
            normalize_monitor_target(monitor_target),
            always_on_top,
        )
        self._start_clipboard_helper()
        self._start_media_watcher(target)
        return {"address": target, "message": "Phone control opened for %s." % target}

    def start_scrcpy(self, settings: PhoneControlSettings) -> dict[str, str]:
        """Open Scrcpy using the configured Wi-Fi or USB ADB connection."""
        return self.connect_and_open(
            settings.address,
            keep_screen_awake=settings.keep_screen_awake,
            turn_screen_off=settings.turn_screen_off,
            always_on_top=settings.always_on_top,
            dock_position=settings.dock_position,
            monitor_target=settings.monitor_target,
            max_size=settings.max_size,
            max_fps=settings.max_fps,
            video_bit_rate=settings.video_bit_rate,
            connection_mode=settings.connection_mode,
        )

    def stop_scrcpy(self) -> None:
        """Stop the active Scrcpy session without altering saved connection preferences."""
        self.close()

    def disconnect(self) -> None:
        """Release the active ADB connection; USB devices remain physically attached."""
        serial = self.connected_serial
        if serial and ":" in serial:
            self.runner.run([self.config.adb_bin, "disconnect", serial], check=False)
        self.connected_serial = ""

    def list_devices(self) -> list[str]:
        """Return ADB devices currently available through either Wi-Fi or USB."""
        completed = self.runner.run([self.config.adb_bin, "devices"], check=False, capture_output=True)
        devices = []
        for raw_line in str(getattr(completed, "stdout", "") or "").splitlines():
            line = raw_line.strip()
            if line and "\tdevice" in line:
                devices.append(line.split("\t", 1)[0].strip())
        return devices

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_connected(self) -> bool:
        return bool(self.connected_serial) or self.is_running()

    def copy_text_to_clipboard(
        self,
        text: str,
        *,
        label: str = "Text",
        address: str = "",
        sync_to_phone: bool = False,
        require_phone_clipboard: bool = True,
    ) -> dict[str, object]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return {"copied": False, "message": "No text to copy."}
        phone_clipboard = False
        phone_clipboard_method = ""
        if sync_to_phone:
            target = normalize_phone_address(address or self.connected_serial)
            self.runner.ensure_tool(self.config.adb_bin)
            phone_clipboard, phone_clipboard_method = self._set_android_clipboard_text(
                target,
                clean_text,
            )
            if not phone_clipboard:
                detail = str(phone_clipboard_method or "").strip()
                if require_phone_clipboard:
                    suffix = " Detail: %s" % detail if detail else ""
                    raise RuntimeError("Could not copy %s to the phone clipboard.%s" % (label, suffix))
        self._set_windows_clipboard_text(clean_text)
        if phone_clipboard:
            message = "%s copied to phone clipboard." % label
        elif sync_to_phone and phone_clipboard_method:
            message = "%s copied to Windows clipboard; phone clipboard sync failed." % label
        else:
            message = "%s copied to clipboard." % label
        self._emit_event(
            "info",
            "phone_text_copied_to_clipboard",
            "%s (%s characters)." % (message, len(clean_text)),
            text_length=len(clean_text),
            label=label,
            phone_clipboard=str(phone_clipboard),
            phone_clipboard_method=phone_clipboard_method,
        )
        return {
            "copied": True,
            "phone_clipboard": phone_clipboard,
            "phone_clipboard_method": phone_clipboard_method,
            "text_length": len(clean_text),
            "message": message,
        }

    def paste_text_with_scrcpy(self, text: str) -> dict[str, object]:
        clean_text = str(text or "")
        if not clean_text:
            return {"pasted": False, "message": "No text to paste."}
        if not self.is_running():
            raise RuntimeError("Open Phone Control before pasting text with scrcpy.")
        self._set_windows_clipboard_text(clean_text)
        self._send_scrcpy_paste_shortcut()
        self._emit_event(
            "info",
            "phone_text_pasted",
            "Pasted text to phone through scrcpy (%s characters)." % len(clean_text),
            text_length=len(clean_text),
        )
        return {
            "pasted": True,
            "text_length": len(clean_text),
            "message": "Pasted text to phone through scrcpy.",
        }

    def paste_text_with_adb_keyboard(
        self,
        address: str,
        text: str,
        *,
        restore_keyboard: bool = True,
    ) -> dict[str, object]:
        target = normalize_phone_address(address or self.connected_serial)
        clean_text = str(text or "")
        if not clean_text:
            return {"pasted": False, "message": "No text to paste."}
        if not target:
            raise RuntimeError("Connect the phone before pasting text with ADBKeyBoard.")
        self.runner.ensure_tool(self.config.adb_bin)
        previous_ime = self._android_default_input_method(target)
        self._set_adb_keyboard_input_method(target)
        if previous_ime and previous_ime != ADB_KEYBOARD_IME:
            self._previous_android_input_method = previous_ime
        time.sleep(ADB_KEYBOARD_READY_DELAY_SECONDS)
        encoded_text = base64.b64encode(clean_text.encode("utf-8")).decode("ascii")
        completed = self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                target,
                "shell",
                "am",
                "broadcast",
                "-a",
                ADB_KEYBOARD_INPUT_B64_ACTION,
                "--es",
                "msg",
                encoded_text,
            ],
            check=False,
        )
        output = self._completed_output(completed)
        if getattr(completed, "returncode", 1) != 0 or self._adb_output_has_error(output):
            raise RuntimeError(
                "Could not paste text with ADBKeyBoard. Install and enable ADBKeyBoard on the phone, then try again. Detail: %s"
                % (output.strip() or "ADB broadcast failed.")
            )
        time.sleep(ADB_KEYBOARD_COMMIT_DELAY_SECONDS)
        if restore_keyboard and previous_ime and previous_ime != ADB_KEYBOARD_IME:
            self.restore_android_input_method(target)
        self._emit_event(
            "info",
            "phone_text_pasted",
            "Pasted text to phone through ADBKeyBoard (%s characters)." % len(clean_text),
            text_length=len(clean_text),
            device_serial=target,
            input_method=ADB_KEYBOARD_IME,
        )
        return {
            "pasted": True,
            "text_length": len(clean_text),
            "message": "Pasted text to phone through ADBKeyBoard.",
        }

    def restore_android_input_method(self, address: str = "") -> dict[str, object]:
        target = normalize_phone_address(address or self.connected_serial)
        previous_ime = self._previous_android_input_method
        if not previous_ime or previous_ime == ADB_KEYBOARD_IME:
            return {"restored": False, "message": "No previous Android keyboard to restore."}
        self.runner.ensure_tool(self.config.adb_bin)
        completed = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "ime", "set", previous_ime],
            check=False,
        )
        output = self._completed_output(completed)
        restored = getattr(completed, "returncode", 1) == 0 and not self._adb_output_has_error(output)
        if restored:
            self._previous_android_input_method = ""
        return {
            "restored": restored,
            "input_method": previous_ime,
            "message": "Android keyboard restored." if restored else "Could not restore Android keyboard.",
        }

    def press_space_and_close_keyboard(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before pressing space.")
        self._send_android_keyevent(target, ANDROID_KEYCODE_SPACE)
        time.sleep(0.15)
        self._send_android_keyevent(target, ANDROID_KEYCODE_BACK)
        self._emit_event(
            "info",
            "phone_keyboard_closed",
            "Pressed space and closed the phone keyboard.",
            device_serial=target,
        )
        return {
            "address": target,
            "message": "Pressed space and closed the phone keyboard.",
        }

    def tap_tiktok_add_link(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping Add link.")
        time.sleep(0.4)
        center = self._tap_ui_text(target, ("\u0054h\u00eam li\u00ean k\u1ebft", "Add link"))
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.5))
            tap_y = max(1, int(height * 0.486))
            self._tap_phone(target, tap_x, tap_y)
        else:
            tap_x, tap_y = center
        self._emit_event(
            "info",
            "phone_add_link_opened",
            "Tapped TikTok Add link at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok Add link.",
        }

    def tap_tiktok_product_link(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping Product.")
        time.sleep(0.5)
        center = self._tap_ui_text(target, ("\u0053\u1ea3n ph\u1ea9m", "Product", "Products"))
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.5))
            tap_y = max(1, int(height * 0.844))
            self._tap_phone(target, tap_x, tap_y)
        else:
            tap_x, tap_y = center
        self._emit_event(
            "info",
            "phone_product_link_opened",
            "Tapped TikTok Product link at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok Product link.",
        }

    def tap_tiktok_product_search_field(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping product search.")
        search_field = None
        for attempt in range(8):
            time.sleep(1.0 if attempt == 0 else 0.7)
            xml_text = self._dump_ui_xml(target)
            search_field = self._find_product_search_field(xml_text)
            if search_field is not None:
                break
        if search_field is None:
            raise RuntimeError(
                "Could not find the TikTok product search text: \u0054\u00ecm ki\u1ebfm s\u1ea3n ph\u1ea9m."
            )
        else:
            tap_x, tap_y = search_field["center"]
        self._tap_phone(target, tap_x, tap_y)
        time.sleep(0.4)
        self._emit_event(
            "info",
            "phone_product_search_focused",
            "Tapped TikTok product search at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
            bounds=str(search_field.get("bounds") or ""),
            text=str(search_field.get("text") or ""),
            content_desc=str(search_field.get("content_desc") or ""),
            class_name=str(search_field.get("class_name") or ""),
            focused=str(search_field.get("focused") or ""),
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "bounds": str(search_field.get("bounds") or ""),
            "text": str(search_field.get("text") or ""),
            "class_name": str(search_field.get("class_name") or ""),
            "focused": str(search_field.get("focused") or ""),
            "message": "Tapped TikTok product search.",
        }

    def search_tiktok_product_id(self, address: str, product_id: str) -> dict[str, object]:
        target = normalize_phone_address(address or self.connected_serial)
        clean_product_id = str(product_id or "").strip()
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before searching product ID.")
        if not clean_product_id:
            raise RuntimeError("Product ID is required before searching TikTok product.")
        if not clean_product_id.isdigit():
            raise RuntimeError("Product ID must contain digits only: %s" % clean_product_id)
        if not self.is_running():
            raise RuntimeError("Open Phone Control before pasting product ID with scrcpy.")
        pasted = False
        for attempt in range(2):
            time.sleep(0.5)
            self._set_windows_clipboard_text(clean_product_id)
            self._send_scrcpy_paste_shortcut()
            time.sleep(1.0)
            xml_text = self._dump_ui_xml(target)
            if clean_product_id in xml_text:
                pasted = True
                break
            if attempt == 0:
                self.tap_tiktok_product_search_field(target)
        if not pasted:
            raise RuntimeError("Could not paste Product ID into TikTok product search.")
        self._send_android_keyevent(target, ANDROID_KEYCODE_ENTER)
        self._emit_event(
            "info",
            "phone_product_id_searched",
            "Pasted product ID and pressed Enter on the phone.",
            device_serial=target,
            product_id=clean_product_id,
        )
        return {
            "address": target,
            "product_id": clean_product_id,
            "message": "Pasted product ID and pressed Enter.",
        }

    def tap_tiktok_product_add_button(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping product Add.")
        time.sleep(1.0)
        center = self._tap_ui_text(target, ("\u0054h\u00eam", "Add", "button_add_product"))
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.817))
            tap_y = max(1, int(height * 0.251))
            self._tap_phone(target, tap_x, tap_y)
        else:
            tap_x, tap_y = center
        self._emit_event(
            "info",
            "phone_product_add_tapped",
            "Tapped TikTok product Add at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok product Add.",
        }

    def tap_optional_tiktok_add_popup(self, address: str = "") -> dict[str, object]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before checking optional Add popup.")
        time.sleep(0.6)
        xml_text = self._dump_ui_xml(target)
        center = self._find_ui_text_center(xml_text, ("\u0054h\u00eam", "Add"))
        source = "text"
        if center is None:
            center = self._find_sparse_center_dialog_add_button(xml_text)
            source = "dialog"
        if center is None:
            return {
                "address": target,
                "tapped": False,
                "message": "No optional Add popup.",
            }
        tap_x, tap_y = center
        self._tap_phone(target, tap_x, tap_y)
        self._emit_event(
            "info",
            "phone_optional_add_popup_tapped",
            "Tapped optional TikTok Add popup at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
            source=source,
        )
        return {
            "address": target,
            "tapped": True,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "source": source,
            "message": "Tapped optional Add popup.",
        }

    def replace_invalid_tiktok_product_name(
        self,
        address: str = "",
        replacement: str = "Mua \u1edf \u0111\u00e2y",
    ) -> dict[str, object]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before replacing product name.")
        clean_replacement = str(replacement or "").strip()
        if not clean_replacement:
            raise RuntimeError("Replacement product name is required.")
        time.sleep(0.5)
        xml_text = self._dump_ui_xml(target)
        field = self._find_product_name_input(xml_text)
        if field is None:
            self._send_android_keyevent(target, ANDROID_KEYCODE_BACK)
            return {
                "address": target,
                "replaced": False,
                "message": "No invalid product name screen; closed keyboard.",
            }
        current_text, center = field
        if current_text.strip() == clean_replacement:
            self._send_android_keyevent(target, ANDROID_KEYCODE_BACK)
            return {
                "address": target,
                "replaced": False,
                "message": "Product name already valid; closed keyboard.",
            }
        if not self._has_disabled_anchor_add_button(xml_text):
            self._send_android_keyevent(target, ANDROID_KEYCODE_BACK)
            return {
                "address": target,
                "replaced": False,
                "message": "Product name screen is already valid; closed keyboard.",
            }
        tap_x, tap_y = center
        self._tap_phone(target, tap_x, tap_y)
        time.sleep(0.2)
        self._send_android_keyevent(target, ANDROID_KEYCODE_MOVE_END)
        delete_count = max(20, len(current_text) + 8)
        for _index in range(delete_count):
            self._send_android_keyevent(target, ANDROID_KEYCODE_DEL)
        self.paste_text_with_scrcpy(clean_replacement)
        time.sleep(0.3)
        self._send_android_keyevent(target, ANDROID_KEYCODE_BACK)
        self._emit_event(
            "info",
            "phone_product_name_replaced",
            "Replaced invalid TikTok product name and closed the keyboard.",
            device_serial=target,
            replacement=clean_replacement,
        )
        return {
            "address": target,
            "replaced": True,
            "replacement": clean_replacement,
            "message": "Replaced invalid product name and closed keyboard.",
        }

    def tap_tiktok_anchor_final_add_button(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping final Add.")
        time.sleep(0.5)
        xml_text = self._dump_ui_xml(target)
        center = self._find_anchor_add_button_center(xml_text, require_enabled=True)
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.5))
            tap_y = max(1, int(height * 0.938))
        else:
            tap_x, tap_y = center
        self._tap_phone(target, tap_x, tap_y)
        self._emit_event(
            "info",
            "phone_anchor_final_add_tapped",
            "Tapped TikTok final Add at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok final Add.",
        }

    def tap_tiktok_more_options(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping More options.")
        time.sleep(0.7)
        center = self._tap_ui_text(target, ("\u0054\u00f9y ch\u1ecdn kh\u00e1c", "More options", "More"))
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.5))
            tap_y = max(1, int(height * 0.642))
            self._tap_phone(target, tap_x, tap_y)
        else:
            tap_x, tap_y = center
        self._emit_event(
            "info",
            "phone_more_options_opened",
            "Tapped TikTok More options at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok More options.",
        }

    def tap_tiktok_schedule_post(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        if not target:
            raise RuntimeError("Connect the phone before tapping Schedule post.")
        time.sleep(0.7)
        center = self._tap_ui_text(target, ("\u004c\u00ean l\u1ecbch \u0111\u0103ng", "Schedule post", "Schedule"))
        if center is None:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.5))
            tap_y = max(1, int(height * 0.667))
            self._tap_phone(target, tap_x, tap_y)
        else:
            tap_x, tap_y = center
        self._emit_event(
            "info",
            "phone_schedule_post_opened",
            "Tapped TikTok Schedule post at %s,%s." % (tap_x, tap_y),
            device_serial=target,
            tap_x=tap_x,
            tap_y=tap_y,
        )
        return {
            "address": target,
            "tap_x": str(tap_x),
            "tap_y": str(tap_y),
            "message": "Tapped TikTok Schedule post.",
        }

    def open_tiktok_upload(self, address: str = "") -> dict[str, str]:
        target = normalize_phone_address(address or self.connected_serial)
        self.runner.ensure_tool(self.config.adb_bin)
        package_name = self._installed_tiktok_package(target)
        attempted: list[object] = []
        opened_deeplink = ""
        for deeplink in TIKTOK_UPLOAD_DEEPLINKS:
            if package_name:
                completed = self.runner.run(
                    [
                        self.config.adb_bin,
                        "-s",
                        target,
                        "shell",
                        "am",
                        "start",
                        "-a",
                        "android.intent.action.VIEW",
                        "-d",
                        deeplink,
                        "-p",
                        package_name,
                    ],
                    check=False,
                )
                attempted.append(completed)
                if completed.returncode == 0:
                    opened_deeplink = deeplink
                    break
            completed = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    deeplink,
                ],
                check=False,
            )
            attempted.append(completed)
            if completed.returncode == 0:
                opened_deeplink = deeplink
                break
        if not opened_deeplink:
            detail = ""
            for completed in reversed(attempted):
                detail = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
                if detail:
                    break
            raise RuntimeError(detail or "Could not open TikTok on the phone.")
        time.sleep(1.2)
        create_x, create_y = self._tap_tiktok_create_button(target)
        time.sleep(1.0)
        library_x, library_y = self._tap_tiktok_library_button(target)
        time.sleep(1.0)
        first_video_x, first_video_y = self._tap_tiktok_first_library_video(target)
        time.sleep(1.0)
        next_x, next_y = self._tap_tiktok_next_button(target)
        time.sleep(1.2)
        caption_x, caption_y = self._tap_tiktok_caption_field(target)
        return self._tiktok_upload_opened(
            target,
            opened_deeplink,
            package_name,
            create_x,
            create_y,
            library_x,
            library_y,
            first_video_x,
            first_video_y,
            next_x,
            next_y,
            caption_x,
            caption_y,
        )

    def send_file_to_gallery(
        self,
        address: str,
        local_path: Path,
        *,
        connection_mode: str = "wifi",
        remote_file_name: str | None = None,
    ) -> dict[str, object]:
        mode = normalize_connection_mode(connection_mode)
        target = normalize_phone_address(address) if mode == "wifi" else ""
        source_path = Path(local_path).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise RuntimeError("Video file does not exist: %s" % source_path)
        if source_path.suffix.lower() not in GALLERY_MEDIA_EXTENSIONS:
            raise RuntimeError("File is not a supported Gallery media file: %s" % source_path.name)
        destination_name = Path(remote_file_name or source_path.name).name
        if not destination_name or destination_name in {".", ".."}:
            raise RuntimeError("A valid destination file name is required.")
        if Path(destination_name).suffix.lower() != source_path.suffix.lower():
            raise RuntimeError("Destination file extension must match the source media file.")

        self.runner.ensure_tool(self.config.adb_bin)
        target = self._connect_adb(
            target,
            connection_mode=mode,
            emit_event=False,
            ensure_push_target=False,
        )
        remote_path = "%s/%s" % (DEFAULT_PUSH_TARGET.rstrip("/"), destination_name)
        self._mark_manual_transfer_path(remote_path)
        size = source_path.stat().st_size
        self._emit_event(
            "info",
            "phone_transfer_started",
            "Sending %s to phone." % source_path.name,
            device_serial=target,
            file_name=source_path.name,
            local_path=str(source_path),
            remote_path=remote_path,
        )
        self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "mkdir", "-p", DEFAULT_PUSH_TARGET],
            check=False,
        )
        self.runner.run(
            [self.config.adb_bin, "-s", target, "push", str(source_path), remote_path],
            check=True,
        )
        self._emit_event(
            "info",
            "phone_transfer_completed",
            "Transfer completed: %s (%s)." % (source_path.name, _format_file_size(size)),
            device_serial=target,
            file_name=source_path.name,
            local_path=str(source_path),
            remote_path=remote_path,
            size_bytes=size,
        )
        for attempt in range(4):
            if attempt:
                time.sleep(0.75)
            if self._scan_media_file(target, remote_path):
                self._emit_event(
                    "info",
                    "phone_gallery_ready",
                    "Ready in Gallery: %s." % source_path.name,
                    device_serial=target,
                    file_name=source_path.name,
                    local_path=str(source_path),
                    remote_path=remote_path,
                    size_bytes=size,
                )
                return {
                    "address": target,
                    "file_name": source_path.name,
                    "local_path": str(source_path),
                    "remote_path": remote_path,
                    "size_bytes": size,
                }

        self._emit_event(
            "warning",
            "phone_transfer_failed",
            "Could not add to Gallery: %s." % source_path.name,
            device_serial=target,
            file_name=source_path.name,
            local_path=str(source_path),
            remote_path=remote_path,
            size_bytes=size,
        )
        raise RuntimeError("Video was copied, but Android Gallery did not index it: %s" % source_path.name)

    def _connect_adb(
        self,
        target: str,
        *,
        connection_mode: str = "wifi",
        emit_event: bool = True,
        ensure_push_target: bool = True,
    ) -> str:
        mode = normalize_connection_mode(connection_mode)
        connection = self.device_transfer.connect(mode, target)
        if not connection.get("connected"):
            raise RuntimeError(str(connection.get("message") or "Could not connect to the phone."))
        selected_serial = str(connection.get("device_serial") or target or "").strip()
        if not selected_serial:
            raise RuntimeError("ADB connected but did not return a device serial.")
        self.connected_serial = selected_serial
        if emit_event:
            self._emit_event(
                "info",
                "phone_connected",
                "Phone connected: %s." % selected_serial,
                device_serial=selected_serial,
            )
        if ensure_push_target:
            self.runner.run(
                [self.config.adb_bin, "-s", selected_serial, "shell", "mkdir", "-p", DEFAULT_PUSH_TARGET],
                check=False,
            )
        return selected_serial

    def _ui(self, target: str) -> UiAutomatorClient | None:
        if self._ui_automation is not None and self._ui_automation_serial == target:
            return self._ui_automation
        try:
            self._ui_automation = UiAutomatorClient(target)
            self._ui_automation_serial = target
            self._ui_automation_error = ""
        except UiAutomatorUnavailable as exc:
            self._ui_automation = None
            self._ui_automation_serial = ""
            self._ui_automation_unavailable = False
            self._ui_automation_error = "uiautomator2 is unavailable for %s: %s" % (
                target,
                str(exc) or exc.__class__.__name__,
            )
        except Exception as exc:
            self._ui_automation = None
            self._ui_automation_serial = ""
            self._ui_automation_error = str(exc) or exc.__class__.__name__
            return None
        return self._ui_automation

    def _tap_phone(self, target: str, tap_x: int, tap_y: int) -> tuple[int, int]:
        ui = self._ui(target)
        if ui is not None:
            try:
                return ui.click_center(tap_x, tap_y)
            except Exception:
                pass
        self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                target,
                "shell",
                "input",
                "tap",
                str(tap_x),
                str(tap_y),
            ],
            check=False,
        )
        return tap_x, tap_y

    def _installed_tiktok_package(self, target: str) -> str:
        for package_name in TIKTOK_ANDROID_PACKAGES:
            completed = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "pm",
                    "path",
                    package_name,
                ],
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return package_name
        return ""

    def _tap_tiktok_create_button(self, target: str) -> tuple[int, int]:
        ui = self._ui(target)
        if ui is not None:
            try:
                return ui.click_ratio(0.5, 0.935)
            except Exception:
                pass
        width, height = self._phone_screen_size(target)
        tap_x = max(1, width // 2)
        tap_y = max(1, int(height * 0.935))
        return self._tap_phone(target, tap_x, tap_y)

    def _tap_tiktok_library_button(self, target: str) -> tuple[int, int]:
        ui = self._ui(target)
        if ui is not None:
            try:
                return ui.click_ratio(0.085, 0.913)
            except Exception:
                pass
        width, height = self._phone_screen_size(target)
        tap_x = max(1, int(width * 0.085))
        tap_y = max(1, int(height * 0.913))
        return self._tap_phone(target, tap_x, tap_y)

    def _tap_tiktok_first_library_video(self, target: str) -> tuple[int, int]:
        ui = self._ui(target)
        if ui is not None:
            try:
                return ui.click_ratio(1 / 6, 0.22)
            except Exception:
                pass
        width, height = self._phone_screen_size(target)
        tap_x = max(1, int(width / 6))
        tap_y = max(1, int(height * 0.22))
        return self._tap_phone(target, tap_x, tap_y)

    def _tap_tiktok_next_button(self, target: str) -> tuple[int, int]:
        text_match = self._tap_ui_text(target, ("Tiếp", "Next"))
        if text_match is not None:
            return text_match
        width, height = self._phone_screen_size(target)
        tap_x = max(1, int(width * 0.735))
        tap_y = max(1, int(height * 0.954))
        return self._tap_phone(target, tap_x, tap_y)

    def _tap_tiktok_caption_field(self, target: str) -> tuple[int, int]:
        xml_text = self._dump_ui_xml(target)
        center = self._find_caption_field_center(xml_text)
        if center is not None:
            tap_x, tap_y = center
        else:
            width, height = self._phone_screen_size(target)
            tap_x = max(1, int(width * 0.22))
            tap_y = max(1, int(height * 0.185))
        return self._tap_phone(target, tap_x, tap_y)

    def _tap_ui_text(self, target: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
        ui = self._ui(target)
        if ui is not None:
            try:
                center = ui.click_text(labels)
                if center is not None:
                    return center
            except Exception:
                pass
        xml_text = self._dump_ui_xml(target)
        center = self._find_ui_text_center(xml_text, labels)
        if center is None:
            return None
        tap_x, tap_y = center
        return self._tap_phone(target, tap_x, tap_y)

    def _find_ui_text_center(self, xml_text: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        wanted = {label.strip().casefold() for label in labels if label.strip()}
        for node in root.iter("node"):
            values = (
                str(node.attrib.get("text") or "").strip(),
                str(node.attrib.get("content-desc") or "").strip(),
            )
            if not any(value.casefold() in wanted for value in values if value):
                continue
            center = self._bounds_center(str(node.attrib.get("bounds") or ""))
            if center is None:
                continue
            return center
        return None

    def _find_product_search_field_center(self, xml_text: str) -> tuple[int, int] | None:
        match = self._find_product_search_field(xml_text)
        if match is None:
            return None
        return match["center"]

    def _find_product_search_field(self, xml_text: str) -> dict[str, object] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        product_search_labels = (
            "\u0054\u00ecm ki\u1ebfm s\u1ea3n ph\u1ea9m",
            "search products",
        )
        wanted = tuple(label.casefold() for label in product_search_labels)
        for node in root.iter("node"):
            text = str(node.attrib.get("text") or "").strip()
            desc = str(node.attrib.get("content-desc") or "").strip()
            values = (text.casefold(), desc.casefold())
            bounds = str(node.attrib.get("bounds") or "")
            center = self._bounds_center(bounds)
            if center is None:
                continue
            if any(label in value for value in values if value for label in wanted):
                return {
                    "center": center,
                    "bounds": bounds,
                    "text": text,
                    "content_desc": desc,
                    "class_name": str(node.attrib.get("class") or ""),
                    "focused": str(node.attrib.get("focused") or ""),
                }
        return None

    def _is_tiktok_shop_product_detail_screen(self, xml_text: str) -> bool:
        if not xml_text:
            return False
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return False
        values = []
        for node in root.iter("node"):
            for attribute in ("text", "content-desc"):
                value = str(node.attrib.get(attribute) or "").strip()
                if value:
                    values.append(value.casefold())
        if not values:
            return False
        has_shop_tab = any(value == "cửa hàng" or value == "shop" for value in values)
        has_chat_tab = any(value == "chat" or "bình luận" in value for value in values)
        has_purchase_action = any(
            marker in value
            for value in values
            for marker in (
                "đặt trước",
                "mua ngay",
                "thêm vào giỏ hàng",
                "add to cart",
                "buy now",
                "pre-order",
                "preorder",
            )
        )
        return has_shop_tab and has_chat_tab and has_purchase_action

    def _find_sparse_center_dialog_add_button(self, xml_text: str) -> tuple[int, int] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        nodes = list(root.iter("node"))
        visible_text = [
            value
            for node in nodes
            for value in (
                str(node.attrib.get("text") or "").strip(),
                str(node.attrib.get("content-desc") or "").strip(),
            )
            if value
        ]
        if len(nodes) > 20 or visible_text:
            return None
        screen_bounds = self._bounds_rect(str(next(root.iter("node")).attrib.get("bounds") or ""))
        if screen_bounds is None:
            return None
        screen_left, screen_top, screen_right, screen_bottom = screen_bounds
        screen_width = max(1, screen_right - screen_left)
        screen_height = max(1, screen_bottom - screen_top)
        for node in nodes:
            bounds = self._bounds_rect(str(node.attrib.get("bounds") or ""))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            width = right - left
            height = bottom - top
            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            if not (screen_width * 0.45 <= width <= screen_width * 0.9):
                continue
            if not (80 <= height <= 360):
                continue
            if abs(center_x - (screen_width // 2)) > screen_width * 0.15:
                continue
            if not (screen_height * 0.25 <= center_y <= screen_height * 0.75):
                continue
            tap_x = left + int(width * 0.78)
            tap_y = top + int(height * 0.74)
            return tap_x, tap_y
        return None

    def _find_product_name_input(self, xml_text: str) -> tuple[str, tuple[int, int]] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        for node in root.iter("node"):
            desc = str(node.attrib.get("content-desc") or "")
            class_name = str(node.attrib.get("class") or "")
            if "edit_anchor_name_input" not in desc and not (
                class_name.endswith("EditText") and "anchor" in desc
            ):
                continue
            center = self._bounds_center(str(node.attrib.get("bounds") or ""))
            if center is None:
                continue
            return str(node.attrib.get("text") or ""), center
        return None

    def _has_disabled_anchor_add_button(self, xml_text: str) -> bool:
        if not xml_text:
            return False
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return False
        for node in root.iter("node"):
            desc = str(node.attrib.get("content-desc") or "").casefold()
            if "edit_anchor_add_button" in desc and "disabled" in desc:
                return True
        return False

    def _find_anchor_add_button_center(self, xml_text: str, *, require_enabled: bool = False) -> tuple[int, int] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        for node in root.iter("node"):
            desc = str(node.attrib.get("content-desc") or "").casefold()
            if "edit_anchor_add_button" not in desc:
                continue
            if require_enabled and "disabled" in desc:
                return None
            center = self._bounds_center(str(node.attrib.get("bounds") or ""))
            if center is not None:
                return center
        return None

    def _find_caption_field_center(self, xml_text: str) -> tuple[int, int] | None:
        if not xml_text:
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        for node in root.iter("node"):
            text = str(node.attrib.get("text") or "").strip().casefold()
            class_name = str(node.attrib.get("class") or "")
            if class_name.endswith("EditText") and ("mô tả" in text or "description" in text):
                center = self._bounds_center(str(node.attrib.get("bounds") or ""))
                if center is not None:
                    return center
        for node in root.iter("node"):
            text = str(node.attrib.get("text") or "").strip().casefold()
            if "mô tả" in text or "description" in text:
                center = self._bounds_center(str(node.attrib.get("bounds") or ""))
                if center is not None:
                    return center
        return None

    def _dump_ui_xml(self, target: str) -> str:
        ui = self._ui(target)
        if ui is not None:
            try:
                xml_text = ui.dump_hierarchy()
                if xml_text:
                    return xml_text
            except Exception:
                pass
        remote_path = "/sdcard/tiktok_tool_window.xml"
        dump_result = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "uiautomator", "dump", remote_path],
            check=False,
        )
        dump_output = "%s\n%s" % (str(dump_result.stdout or ""), str(dump_result.stderr or ""))
        if "ERROR:" in dump_output and "dumped to" not in dump_output.casefold():
            return ""
        completed = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "cat", remote_path],
            check=False,
        )
        return str(completed.stdout or "")

    def _bounds_center(self, bounds: str) -> tuple[int, int] | None:
        rect = self._bounds_rect(bounds)
        if rect is None:
            return None
        left, top, right, bottom = rect
        return (left + right) // 2, (top + bottom) // 2

    def _bounds_rect(self, bounds: str) -> tuple[int, int, int, int] | None:
        match = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
        if not match:
            return None
        return tuple(int(value) for value in match.groups())

    def _set_windows_clipboard_text(self, text: str) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p

        data = (text + "\0").encode("utf-16le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise RuntimeError("Could not allocate Windows clipboard memory.")
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            raise RuntimeError("Could not lock Windows clipboard memory.")
        try:
            ctypes.memmove(locked, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            raise RuntimeError("Could not open Windows clipboard.")
        try:
            if not user32.EmptyClipboard():
                raise RuntimeError("Could not clear Windows clipboard.")
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("Could not set Windows clipboard text.")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    def _send_scrcpy_paste_shortcut(self) -> None:
        user32 = ctypes.windll.user32
        user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        user32.MapVirtualKeyW.restype = wintypes.UINT
        hwnd = WindowsScrcpyDock._find_process_window(self.process, require_title=False) if self.process else 0
        if not hwnd:
            hwnd = user32.FindWindowW(None, SCRCPY_WINDOW_TITLE)
        if not hwnd:
            raise RuntimeError("Could not find the scrcpy window.")
        rctrl_scan = user32.MapVirtualKeyW(VK_RCONTROL, MAPVK_VK_TO_VSC)
        v_scan = user32.MapVirtualKeyW(VK_V, MAPVK_VK_TO_VSC)
        for _attempt in range(3):
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            if user32.GetForegroundWindow() == hwnd:
                break
        if user32.GetForegroundWindow() != hwnd:
            self._post_scrcpy_paste_shortcut(hwnd, rctrl_scan, v_scan)
            return
        user32.keybd_event(VK_RCONTROL, rctrl_scan, KEYEVENTF_EXTENDEDKEY, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, v_scan, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, v_scan, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_RCONTROL, rctrl_scan, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    def _post_scrcpy_paste_shortcut(self, hwnd: int, rctrl_scan: int, v_scan: int) -> None:
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL

        def lparam(scan_code: int, *, extended: bool = False, keyup: bool = False) -> int:
            value = 1 | (int(scan_code or 0) << 16)
            if extended:
                value |= 1 << 24
            if keyup:
                value |= (1 << 30) | (1 << 31)
            return value

        messages = (
            (WM_KEYDOWN, VK_RCONTROL, lparam(rctrl_scan, extended=True)),
            (WM_KEYDOWN, VK_V, lparam(v_scan)),
            (WM_KEYUP, VK_V, lparam(v_scan, keyup=True)),
            (WM_KEYUP, VK_RCONTROL, lparam(rctrl_scan, extended=True, keyup=True)),
        )
        for message, virtual_key, params in messages:
            if not user32.PostMessageW(hwnd, message, virtual_key, params):
                raise RuntimeError("Could not send paste shortcut to the scrcpy window.")
            time.sleep(0.03)

    def _send_android_keyevent(self, target: str, keycode: str) -> None:
        self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "input", "keyevent", keycode],
            check=False,
        )

    def _android_default_input_method(self, target: str) -> str:
        completed = self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                target,
                "shell",
                "settings",
                "get",
                "secure",
                "default_input_method",
            ],
            check=False,
        )
        if getattr(completed, "returncode", 1) != 0:
            return ""
        text = str(getattr(completed, "stdout", "") or "").strip()
        return "" if text == "null" else text

    def _set_adb_keyboard_input_method(self, target: str) -> None:
        enable = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "ime", "enable", ADB_KEYBOARD_IME],
            check=False,
        )
        set_ime = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "ime", "set", ADB_KEYBOARD_IME],
            check=False,
        )
        output = "%s\n%s" % (self._completed_output(enable), self._completed_output(set_ime))
        if (
            getattr(set_ime, "returncode", 1) != 0
            or "Unknown input method" in output
            or "Unknown id" in output
            or "Error" in output
        ):
            if self._install_adb_keyboard_if_available(target):
                enable = self.runner.run(
                    [self.config.adb_bin, "-s", target, "shell", "ime", "enable", ADB_KEYBOARD_IME],
                    check=False,
                )
                set_ime = self.runner.run(
                    [self.config.adb_bin, "-s", target, "shell", "ime", "set", ADB_KEYBOARD_IME],
                    check=False,
                )
                output = "%s\n%s" % (self._completed_output(enable), self._completed_output(set_ime))
                if (
                    getattr(set_ime, "returncode", 1) == 0
                    and "Unknown input method" not in output
                    and "Unknown id" not in output
                    and "Error" not in output
                ):
                    return
            apk_path = self._adb_keyboard_apk_path()
            install_hint = (
                " Put ADBKeyboard.apk at %s and try again." % (Path.cwd() / "tools" / "ADBKeyboard.apk")
                if apk_path is None
                else ""
            )
            raise RuntimeError(
                "ADBKeyBoard is not available on the phone and automatic install failed.%s Enable it once with: adb shell ime enable %s"
                % (install_hint, ADB_KEYBOARD_IME)
            )

    def _install_adb_keyboard_if_available(self, target: str) -> bool:
        apk_path = self._adb_keyboard_apk_path()
        if apk_path is None:
            return False
        completed = self.runner.run(
            [self.config.adb_bin, "-s", target, "install", "-r", str(apk_path)],
            check=False,
        )
        output = self._completed_output(completed)
        return getattr(completed, "returncode", 1) == 0 and (
            "Success" in output or not output.strip()
        )

    def _adb_keyboard_apk_path(self) -> Path | None:
        candidates = []
        env_path = os.getenv("AUTO_EDITOR_ADB_KEYBOARD_APK", "").strip()
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend(
            [
                Path.cwd() / "tools" / "ADBKeyboard.apk",
                Path.cwd() / "ADBKeyboard.apk",
                Path(__file__).resolve().parents[2] / "tools" / "ADBKeyboard.apk",
                Path(sys.executable).resolve().parent / "tools" / "ADBKeyboard.apk",
            ]
        )
        for candidate in candidates:
            try:
                path = candidate.expanduser().resolve()
            except OSError:
                continue
            if path.is_file():
                return path
        return None

    @staticmethod
    def _completed_output(completed: object) -> str:
        return "%s\n%s" % (
            str(getattr(completed, "stdout", "") or ""),
            str(getattr(completed, "stderr", "") or ""),
        )

    @staticmethod
    def _adb_output_has_error(output: str) -> bool:
        lowered = str(output or "").casefold()
        return any(
            marker in lowered
            for marker in (
                "exception",
                "securityexception",
                "permission denial",
                "not found",
                "unknown",
                "error:",
            )
        )

    def _set_android_clipboard_text(self, target: str, text: str) -> tuple[bool, str]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return False, ""
        failure_reasons: list[str] = []
        ui = self._ui(target)
        if ui is not None:
            try:
                ui.set_clipboard(clean_text)
                return True, "uiautomator2"
            except Exception as exc:
                failure_reasons.append("uiautomator2 failed: %s" % (str(exc) or exc.__class__.__name__))
        elif self._ui_automation_error:
            failure_reasons.append(self._ui_automation_error)
        completed = self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                target,
                "shell",
                "cmd",
                "clipboard",
                "set",
                clean_text,
            ],
            check=False,
        )
        set_output = "%s\n%s" % (
            getattr(completed, "stdout", "") or "",
            getattr(completed, "stderr", "") or "",
        )
        if getattr(completed, "returncode", 1) != 0 or "No shell command implementation" in set_output:
            failure_reasons.append("adb cmd clipboard set failed: %s" % set_output.strip())
            return False, "; ".join(reason for reason in failure_reasons if reason)
        verify_output = ""
        for attempt in range(ANDROID_CLIPBOARD_VERIFY_ATTEMPTS):
            verify = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "cmd",
                    "clipboard",
                    "get",
                ],
                check=False,
            )
            verify_output = "%s\n%s" % (
                getattr(verify, "stdout", "") or "",
                getattr(verify, "stderr", "") or "",
            )
            if (
                getattr(verify, "returncode", 1) == 0
                and "No shell command implementation" not in verify_output
                and clean_text in verify_output
            ):
                return True, "adb_cmd_clipboard"
            if attempt + 1 < ANDROID_CLIPBOARD_VERIFY_ATTEMPTS:
                time.sleep(ANDROID_CLIPBOARD_VERIFY_DELAY_SECONDS)
        failure_reasons.append("adb cmd clipboard verify failed: %s" % verify_output.strip())
        return False, "; ".join(reason for reason in failure_reasons if reason)

    def _phone_screen_size(self, target: str) -> tuple[int, int]:
        ui = self._ui(target)
        if ui is not None:
            try:
                return ui.window_size()
            except Exception:
                pass
        completed = self.runner.run(
            [self.config.adb_bin, "-s", target, "shell", "wm", "size"],
            check=False,
        )
        match = re.search(r"(\d+)x(\d+)", completed.stdout or "")
        if not match:
            return 1080, 2400
        return int(match.group(1)), int(match.group(2))

    def _tiktok_upload_opened(
        self,
        target: str,
        deeplink: str,
        package_name: str,
        create_x: int,
        create_y: int,
        library_x: int,
        library_y: int,
        first_video_x: int,
        first_video_y: int,
        next_x: int,
        next_y: int,
        caption_x: int,
        caption_y: int,
    ) -> dict[str, str]:
        message = "Opened publish screen and focused caption at %s,%s." % (caption_x, caption_y)
        self._emit_event(
            "info",
            "phone_tiktok_upload_opened",
            message,
            device_serial=target,
            deeplink=deeplink,
            package_name=package_name,
            create_x=create_x,
            create_y=create_y,
            library_x=library_x,
            library_y=library_y,
            first_video_x=first_video_x,
            first_video_y=first_video_y,
            next_x=next_x,
            next_y=next_y,
            caption_x=caption_x,
            caption_y=caption_y,
        )
        return {
            "address": target,
            "deeplink": deeplink,
            "package_name": package_name,
            "create_x": str(create_x),
            "create_y": str(create_y),
            "library_x": str(library_x),
            "library_y": str(library_y),
            "first_video_x": str(first_video_x),
            "first_video_y": str(first_video_y),
            "next_x": str(next_x),
            "next_y": str(next_y),
            "caption_x": str(caption_x),
            "caption_y": str(caption_y),
            "message": message,
        }

    def _mark_manual_transfer_path(self, path: str) -> None:
        with self._manual_transfer_lock:
            self._manual_transfer_paths.add(path)

    def _is_manual_transfer_path(self, path: str) -> bool:
        with self._manual_transfer_lock:
            return path in self._manual_transfer_paths

    def _start_scrcpy_monitor(self, process: subprocess.Popen, device_serial: str) -> None:
        if getattr(process, "stderr", None) is None:
            return
        self._scrcpy_monitor_thread = threading.Thread(
            target=self._monitor_scrcpy_process,
            args=(process, device_serial),
            daemon=True,
            name="scrcpy-monitor",
        )
        self._scrcpy_monitor_thread.start()

    def _monitor_scrcpy_process(
        self,
        process: subprocess.Popen,
        device_serial: str,
    ) -> None:
        recent_lines: list[str] = []
        stderr = process.stderr
        if stderr is not None:
            for raw_line in stderr:
                line = raw_line.strip()
                if line:
                    recent_lines.append(line)
                    del recent_lines[:-8]
        return_code = process.wait()
        if self.process is not process:
            return

        self.process = None
        self._stop_window_dock()
        self._stop_media_watcher()
        self._stop_clipboard_helper()
        detail = recent_lines[-1] if recent_lines else "scrcpy exited without an error message."
        self._emit_event(
            "error" if return_code else "warning",
            "scrcpy_closed",
            "Phone control stopped unexpectedly (exit code %s): %s" % (return_code, detail),
            device_serial=device_serial,
            exit_code=return_code,
            scrcpy_log="\n".join(recent_lines),
        )

    def _start_window_dock(
        self,
        process: subprocess.Popen,
        position: str,
        monitor_target: str,
        always_on_top: bool,
    ) -> None:
        self._stop_window_dock()
        if position == "off":
            return
        self._window_dock_stop.clear()
        self._window_dock_thread = threading.Thread(
            target=self._dock_scrcpy_window,
            args=(process, position, monitor_target, always_on_top),
            daemon=True,
            name="scrcpy-window-dock",
        )
        self._window_dock_thread.start()

    def _dock_scrcpy_window(
        self,
        process: subprocess.Popen,
        position: str,
        monitor_target: str,
        always_on_top: bool,
    ) -> None:
        docked = self._window_dock.attach(
            process,
            position,
            monitor_target=monitor_target,
            always_on_top=always_on_top,
            stop_event=self._window_dock_stop,
        )
        if self._window_dock_stop.is_set():
            return
        if docked:
            selected_monitor = self._window_dock.selected_monitor
            fallback = monitor_target == "secondary" and selected_monitor == "primary"
            message = "scrcpy docked to the %s of the %s monitor." % (
                position,
                selected_monitor,
            )
            if fallback:
                message += " Secondary monitor was not available."
            self._emit_event(
                "warning" if fallback else "info",
                "phone_docked",
                message,
                dock_position=position,
                monitor_target=selected_monitor,
            )
        else:
            self._emit_event(
                "warning",
                "phone_dock_failed",
                "Could not dock scrcpy; phone control remains open.",
                dock_position=position,
            )

    def _stop_window_dock(self) -> None:
        self._window_dock_stop.set()
        self._window_dock.remove()
        thread = self._window_dock_thread
        self._window_dock_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def capture_screenshot(
        self,
        address: str,
        output_dir: Path | str,
        *,
        copy_to_clipboard: bool = False,
    ) -> dict[str, str]:
        target = normalize_phone_address(address)
        self.runner.ensure_tool(self.config.adb_bin)
        screenshot_dir = Path(output_dir).expanduser().resolve()
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        screenshot_path = screenshot_dir / ("phone_%s.png" % timestamp)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            with screenshot_path.open("wb") as output:
                completed = subprocess.run(
                    [
                        self.config.adb_bin,
                        "-s",
                        target,
                        "exec-out",
                        "screencap",
                        "-p",
                    ],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=False,
                    creationflags=creationflags,
                    close_fds=True,
                )
            if completed.returncode != 0:
                error = completed.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(error or "Could not capture the phone screen.")
            if screenshot_path.stat().st_size < 8:
                raise RuntimeError("The phone returned an empty screenshot.")
            with screenshot_path.open("rb") as screenshot:
                if screenshot.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise RuntimeError("The phone returned an invalid screenshot.")
        except Exception:
            for _attempt in range(4):
                try:
                    screenshot_path.unlink(missing_ok=True)
                    break
                except OSError:
                    time.sleep(0.1)
            raise

        if copy_to_clipboard:
            self._copy_image_to_clipboard(screenshot_path)
            message = "Screenshot copied to clipboard: %s." % screenshot_path.name
        else:
            message = "Screenshot saved: %s." % screenshot_path.name
        self._emit_event(
            "info",
            "phone_screenshot_saved",
            message,
            device_serial=target,
            file_name=screenshot_path.name,
            local_path=str(screenshot_path),
        )
        return {
            "address": target,
            "path": str(screenshot_path),
            "message": message,
        }

    def _start_clipboard_helper(self) -> bool:
        process = self._clipboard_process
        if process is not None and process.poll() is None:
            return True
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "[Console]::Out.WriteLine('READY');"
            "[Console]::Out.Flush();"
            "while (($path=[Console]::In.ReadLine()) -ne $null) {"
            "try {"
            "$image=[System.Drawing.Image]::FromFile($path);"
            "try {[System.Windows.Forms.Clipboard]::SetImage($image)} "
            "finally {$image.Dispose()};"
            "[Console]::Out.WriteLine('OK')"
            "} catch {"
            "[Console]::Out.WriteLine('ERROR`t' + $_.Exception.Message)"
            "};"
            "[Console]::Out.Flush()"
            "}"
        )
        try:
            self._clipboard_process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-Command",
                    script,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=False,
            )
        except Exception:
            self._clipboard_process = None
            return False
        process = self._clipboard_process
        if process.stdout is None or process.stdout.readline().strip() != "READY":
            self._stop_clipboard_helper()
            return False
        return True

    def _stop_clipboard_helper(self) -> None:
        process = self._clipboard_process
        self._clipboard_process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except Exception:
            terminate_process_tree(process, timeout=2)

    def _copy_image_to_clipboard(self, screenshot_path: Path) -> None:
        with self._clipboard_lock:
            if not self._start_clipboard_helper():
                raise RuntimeError("Could not start the clipboard helper.")
            process = self._clipboard_process
            if process is None or process.stdin is None or process.stdout is None:
                raise RuntimeError("Clipboard helper is unavailable.")
            try:
                process.stdin.write(str(screenshot_path) + "\n")
                process.stdin.flush()
                response = process.stdout.readline().strip()
            except Exception as exc:
                self._stop_clipboard_helper()
                raise RuntimeError("Could not communicate with the clipboard helper.") from exc
            if response != "OK":
                message = response.split("\t", 1)[-1] if response else "Clipboard helper stopped."
                self._stop_clipboard_helper()
                raise RuntimeError(message)

    def close(self) -> None:
        self._stop_window_dock()
        self._stop_media_watcher()
        self._stop_clipboard_helper()
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        terminate_process_tree(process, timeout=3)
        monitor_thread = self._scrcpy_monitor_thread
        self._scrcpy_monitor_thread = None
        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=2)
        self._emit_event("info", "scrcpy_closed", "Phone control closed.")

    def cleanup(self, *, stop_adb_server: bool = True) -> None:
        """Release every phone-control resource owned by the application."""
        try:
            self.close()
        finally:
            try:
                self.disconnect()
            finally:
                if stop_adb_server:
                    self.runner.run([self.config.adb_bin, "kill-server"], check=False)

    def _start_media_watcher(self, device_serial: str) -> None:
        self._stop_media_watcher()
        self._media_watcher_stop.clear()
        self._media_watcher_thread = threading.Thread(
            target=self._media_watcher_loop,
            args=(device_serial,),
            daemon=True,
            name="phone-media-watcher",
        )
        self._media_watcher_thread.start()

    def _stop_media_watcher(self) -> None:
        self._media_watcher_stop.set()
        event_process = self._media_event_process
        self._media_event_process = None
        if event_process is not None and event_process.poll() is None:
            terminate_process_tree(event_process, timeout=2)
        thread = self._media_watcher_thread
        self._media_watcher_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _media_watcher_loop(self, device_serial: str) -> None:
        if self._watch_media_events(device_serial):
            return
        if self._media_watcher_stop.is_set() or not self.is_running():
            return
        self._media_polling_loop(device_serial, self._list_target_files(device_serial))

    def _watch_media_events(self, device_serial: str) -> bool:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [
                    self.config.adb_bin,
                    "-s",
                    device_serial,
                    "shell",
                    "inotifyd",
                    "-",
                    "%s:nwd" % DEFAULT_PUSH_TARGET.rstrip("/"),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
                close_fds=False,
            )
        except Exception:
            return False

        self._media_event_process = process
        recently_processed: dict[str, float] = {}
        try:
            if process.stdout is None:
                return False
            for line in process.stdout:
                if self._media_watcher_stop.is_set():
                    return True
                path = self._media_path_from_event(line)
                if path is None:
                    continue
                if self._is_manual_transfer_path(path):
                    continue
                now = time.monotonic()
                if now - recently_processed.get(path, 0.0) < 10:
                    continue
                recently_processed[path] = now
                self._process_completed_media_file(device_serial, path)
        finally:
            if self._media_event_process is process:
                self._media_event_process = None
            if process.poll() is None:
                terminate_process_tree(process, timeout=2)
        return self._media_watcher_stop.is_set() or not self.is_running()

    @staticmethod
    def _media_path_from_event(line: str) -> str | None:
        parts = str(line or "").strip().split("\t", 2)
        if len(parts) != 3:
            return None
        events, directory, file_name = parts
        if "w" not in events or not file_name:
            return None
        path = "%s/%s" % (directory.rstrip("/"), file_name)
        if Path(path).suffix.lower() not in GALLERY_MEDIA_EXTENSIONS:
            return None
        return path

    def _process_completed_media_file(self, device_serial: str, path: str) -> None:
        size = self._file_size(device_serial, path)
        self._emit_event(
            "info",
            "phone_transfer_started",
            "Receiving %s from computer." % Path(path).name,
            device_serial=device_serial,
            file_name=Path(path).name,
            remote_path=path,
        )
        self._emit_event(
            "info",
            "phone_transfer_completed",
            "Transfer completed: %s (%s)." % (Path(path).name, _format_file_size(size)),
            device_serial=device_serial,
            file_name=Path(path).name,
            remote_path=path,
            size_bytes=size or 0,
        )
        for attempt in range(4):
            if attempt and self._media_watcher_stop.wait(0.75):
                return
            if self._scan_media_file(device_serial, path):
                self._emit_event(
                    "info",
                    "phone_gallery_ready",
                    "Ready in Gallery: %s." % Path(path).name,
                    device_serial=device_serial,
                    file_name=Path(path).name,
                    remote_path=path,
                    size_bytes=size or 0,
                )
                return
        self._emit_event(
            "warning",
            "phone_transfer_failed",
            "Could not add to Gallery: %s." % Path(path).name,
            device_serial=device_serial,
            file_name=Path(path).name,
            remote_path=path,
        )

    def _media_polling_loop(self, device_serial: str, known_files: set[str]) -> None:
        pending_files: dict[str, tuple[int | None, int]] = {}
        completed_files: set[str] = set()
        while not self._media_watcher_stop.wait(MEDIA_SCAN_INTERVAL_SECONDS):
            current_files = self._list_target_files(device_serial)
            known_files.intersection_update(current_files)
            for path in current_files - known_files - pending_files.keys():
                if self._is_manual_transfer_path(path):
                    known_files.add(path)
                    continue
                if Path(path).suffix.lower() in GALLERY_MEDIA_EXTENSIONS:
                    pending_files[path] = (None, 0)
                    self._emit_event(
                        "info",
                        "phone_transfer_started",
                        "Receiving %s from computer." % Path(path).name,
                        device_serial=device_serial,
                        file_name=Path(path).name,
                        remote_path=path,
                    )

            for path in list(pending_files):
                if path not in current_files:
                    pending_files.pop(path, None)
                    completed_files.discard(path)
                    self._emit_event(
                        "warning",
                        "phone_transfer_failed",
                        "Transfer disappeared before completion: %s." % Path(path).name,
                        device_serial=device_serial,
                        file_name=Path(path).name,
                        remote_path=path,
                    )
                    continue
                size = self._file_size(device_serial, path)
                previous_size, stable_checks = pending_files[path]
                if size is not None and size > 0 and size == previous_size:
                    stable_checks += 1
                else:
                    stable_checks = 0
                if stable_checks >= 2:
                    if path not in completed_files:
                        completed_files.add(path)
                        self._emit_event(
                            "info",
                            "phone_transfer_completed",
                            "Transfer completed: %s (%s)." % (Path(path).name, _format_file_size(size)),
                            device_serial=device_serial,
                            file_name=Path(path).name,
                            remote_path=path,
                            size_bytes=size or 0,
                        )
                    if self._scan_media_file(device_serial, path):
                        known_files.add(path)
                        pending_files.pop(path, None)
                        completed_files.discard(path)
                        self._emit_event(
                            "info",
                            "phone_gallery_ready",
                            "Ready in Gallery: %s." % Path(path).name,
                            device_serial=device_serial,
                            file_name=Path(path).name,
                            remote_path=path,
                            size_bytes=size or 0,
                        )
                else:
                    pending_files[path] = (size, stable_checks)
            if not self.is_running():
                return

    def _list_target_files(self, device_serial: str) -> set[str]:
        try:
            completed = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    device_serial,
                    "shell",
                    "find",
                    DEFAULT_PUSH_TARGET.rstrip("/"),
                    "-maxdepth",
                    "1",
                    "-type",
                    "f",
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            return set()
        if completed.returncode != 0:
            return set()
        return {line.strip() for line in completed.stdout.splitlines() if line.strip()}

    def _file_size(self, device_serial: str, path: str) -> int | None:
        try:
            completed = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    device_serial,
                    "shell",
                    "stat",
                    "-c",
                    "%s",
                    path,
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        try:
            return int(completed.stdout.strip())
        except (TypeError, ValueError):
            return None

    def _scan_media_file(self, device_serial: str, path: str) -> bool:
        transferred_at = int(time.time())
        self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                device_serial,
                "shell",
                "touch",
                "-m",
                path,
            ],
            check=False,
            capture_output=True,
        )
        media_uri = "file://" + quote(path, safe="/:")
        self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                device_serial,
                "shell",
                "am",
                "broadcast",
                "--receiver-include-background",
                "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d",
                media_uri,
            ],
            check=False,
            capture_output=True,
        )
        return self._finalize_media_file(device_serial, path, transferred_at)

    def _finalize_media_file(
        self,
        device_serial: str,
        path: str,
        transferred_at: int | None = None,
    ) -> bool:
        timestamp_seconds = int(time.time()) if transferred_at is None else int(transferred_at)
        timestamp_milliseconds = timestamp_seconds * 1000
        media_path = path.replace("/sdcard/", "/storage/emulated/0/", 1)
        collection = "images" if Path(path).suffix.lower() in GALLERY_IMAGE_EXTENSIONS else "video"
        collection_uri = "content://media/external/%s/media" % collection
        try:
            completed = self.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    device_serial,
                    "shell",
                    "content",
                    "query",
                    "--uri",
                    collection_uri,
                    "--projection",
                    "_id:_data:is_pending",
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            return False
        if completed.returncode != 0:
            return False

        media_id = None
        for line in completed.stdout.splitlines():
            if "_data=%s" % media_path not in line:
                continue
            match = re.search(r"\b_id=(\d+)", line)
            if match is not None:
                media_id = match.group(1)
                break
        if media_id is None:
            return False

        completed = self.runner.run(
            [
                self.config.adb_bin,
                "-s",
                device_serial,
                "shell",
                "content",
                "update",
                "--uri",
                "%s/%s" % (collection_uri, media_id),
                "--bind",
                "is_pending:i:0",
                "--bind",
                "date_added:l:%d" % timestamp_seconds,
                "--bind",
                "date_modified:l:%d" % timestamp_seconds,
                "--bind",
                "datetaken:l:%d" % timestamp_milliseconds,
            ],
            check=False,
            capture_output=True,
        )
        return completed.returncode == 0

    def _emit_event(self, level: str, action: str, message: str, **details: object) -> None:
        if self.on_event is None:
            return
        payload = {
            "level": level,
            "action": action,
            "message": message,
        }
        payload.update(details)
        try:
            self.on_event(payload)
        except Exception:
            pass


def _format_file_size(size_bytes: int | None) -> str:
    size = float(max(0, int(size_bytes or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % int(size)
        size /= 1024
    return "%d B" % int(size_bytes or 0)
