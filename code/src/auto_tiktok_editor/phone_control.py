"""ADB and scrcpy integration for controlling an Android phone."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable
from urllib.parse import quote
from ctypes import wintypes

from auto_tiktok_editor.app.device_transfer import AndroidDeviceTransfer
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.utils.command import CommandRunner


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
DOCK_POSITIONS = {"off", "left", "right"}
MONITOR_TARGETS = {"primary", "secondary"}
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
    def _find_process_window(process: subprocess.Popen) -> int:
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
                return True
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == SCRCPY_WINDOW_TITLE:
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

    def connect(self, address: str) -> dict[str, str]:
        target = normalize_phone_address(address)
        self.runner.ensure_tool(self.config.adb_bin)
        self._connect_adb(target)
        return {
            "address": target,
            "message": "Phone connected for file transfer: %s." % target,
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
    ) -> dict[str, str]:
        target = normalize_phone_address(address)
        if self.is_running():
            return {"address": target, "message": "Phone control is already open."}

        self.runner.ensure_tool(self.config.adb_bin)
        self.runner.ensure_tool(self.config.scrcpy_bin)
        self._connect_adb(target)
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

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_connected(self) -> bool:
        return bool(self.connected_serial) or self.is_running()

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
        return self._tiktok_upload_opened(
            target,
            opened_deeplink,
            package_name,
            create_x,
            create_y,
            library_x,
            library_y,
        )

    def send_file_to_gallery(self, address: str, local_path: Path) -> dict[str, object]:
        target = normalize_phone_address(address)
        source_path = Path(local_path).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise RuntimeError("Video file does not exist: %s" % source_path)
        if source_path.suffix.lower() not in GALLERY_MEDIA_EXTENSIONS:
            raise RuntimeError("File is not a supported Gallery media file: %s" % source_path.name)

        self.runner.ensure_tool(self.config.adb_bin)
        self._connect_adb(target, emit_event=False, ensure_push_target=False)
        remote_path = "%s/%s" % (DEFAULT_PUSH_TARGET.rstrip("/"), source_path.name)
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
        emit_event: bool = True,
        ensure_push_target: bool = True,
    ) -> None:
        connection = self.device_transfer.connect("wifi", target)
        if not connection.get("connected"):
            raise RuntimeError(str(connection.get("message") or "Could not connect to the phone."))
        self.connected_serial = target
        if emit_event:
            self._emit_event(
                "info",
                "phone_connected",
                "Phone connected: %s." % target,
                device_serial=target,
            )
        if ensure_push_target:
            self.runner.run(
                [self.config.adb_bin, "-s", target, "shell", "mkdir", "-p", DEFAULT_PUSH_TARGET],
                check=False,
            )

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
        width, height = self._phone_screen_size(target)
        tap_x = max(1, width // 2)
        tap_y = max(1, int(height * 0.935))
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

    def _tap_tiktok_library_button(self, target: str) -> tuple[int, int]:
        width, height = self._phone_screen_size(target)
        tap_x = max(1, int(width * 0.085))
        tap_y = max(1, int(height * 0.913))
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

    def _phone_screen_size(self, target: str) -> tuple[int, int]:
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
    ) -> dict[str, str]:
        message = "Opened TikTok and tapped Library at %s,%s." % (library_x, library_y)
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
        )
        return {
            "address": target,
            "deeplink": deeplink,
            "package_name": package_name,
            "create_x": str(create_x),
            "create_y": str(create_y),
            "library_x": str(library_x),
            "library_y": str(library_y),
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
            try:
                process.terminate()
            except Exception:
                pass

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
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        monitor_thread = self._scrcpy_monitor_thread
        self._scrcpy_monitor_thread = None
        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=2)
        self._emit_event("info", "scrcpy_closed", "Phone control closed.")

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
            try:
                event_process.terminate()
            except Exception:
                pass
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
                try:
                    process.terminate()
                except Exception:
                    pass
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
