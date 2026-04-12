"""Helpers for connecting to Android devices and pushing session outputs via ADB."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.utils.command import CommandRunner


SAFE_REMOTE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class AndroidDeviceTransfer(object):
    def __init__(
        self,
        config: PipelineConfig,
        runner: CommandRunner,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config
        self.runner = runner
        self.logger = logger or logging.getLogger(__name__)

    def connect(self, mode: str, address: str = "") -> Dict[str, object]:
        normalized_mode = (mode or "usb").strip().lower()
        self.runner.run([self.config.adb_bin, "start-server"], check=False)

        preferred_serial = (self.config.android_device_serial or "").strip()
        if normalized_mode == "wifi":
            target = address.strip()
            if not target:
                return {
                    "connected": False,
                    "mode": normalized_mode,
                    "device_serial": None,
                    "devices": [],
                    "message": "Nhập địa chỉ IP:PORT của điện thoại để kết nối Wi-Fi.",
                }
            try:
                self.runner.run([self.config.adb_bin, "connect", target])
            except Exception as exc:
                return {
                    "connected": False,
                    "mode": normalized_mode,
                    "device_serial": None,
                    "devices": [],
                    "message": "Không thể kết nối ADB qua Wi-Fi: %s" % exc,
                }
            preferred_serial = target

        device_serials, warning = self._list_connected_devices()
        if warning:
            return {
                "connected": False,
                "mode": normalized_mode,
                "device_serial": None,
                "devices": [],
                "message": warning,
            }

        selected_serial = self._select_device_serial(device_serials, preferred_serial)
        if selected_serial is None:
            message = "Không tìm thấy điện thoại ADB nào sẵn sàng."
            if normalized_mode == "wifi":
                message = "ADB đã chạy nhưng chưa thấy thiết bị Wi-Fi vừa kết nối."
            return {
                "connected": False,
                "mode": normalized_mode,
                "device_serial": None,
                "devices": device_serials,
                "message": message,
            }

        return {
            "connected": True,
            "mode": normalized_mode,
            "device_serial": selected_serial,
            "devices": device_serials,
            "message": "Đã kết nối thiết bị %s." % selected_serial,
        }

    def push_session_outputs(
        self,
        video_paths: List[Path],
        titles_path: Optional[Path] = None,
        session_label: str = "",
        device_serial: str = "",
    ) -> Dict[str, object]:
        device_serials, warning = self._list_connected_devices()
        if warning:
            return {
                "attempted": False,
                "pushed_count": 0,
                "device_serial": None,
                "remote_dir": None,
                "warnings": [warning],
                "transferred_files": [],
            }

        selected_serial = self._select_device_serial(device_serials, device_serial.strip())
        if selected_serial is None:
            return {
                "attempted": False,
                "pushed_count": 0,
                "device_serial": None,
                "remote_dir": None,
                "warnings": ["Chưa có điện thoại ADB sẵn sàng để nhận video."],
                "transferred_files": [],
            }

        remote_root = self.config.android_device_video_dir.rstrip("/")
        remote_dir = remote_root + "/" + self._safe_remote_name(session_label or "session_outputs")
        command_prefix = self._adb_base_args(selected_serial)
        warnings = []
        transferred_files = []

        try:
            self.runner.run(command_prefix + ["shell", "mkdir", "-p", remote_dir])
        except Exception as exc:
            return {
                "attempted": True,
                "pushed_count": 0,
                "device_serial": selected_serial,
                "remote_dir": remote_dir,
                "warnings": ["Không thể tạo thư mục trên điện thoại: %s" % exc],
                "transferred_files": [],
            }

        if titles_path is not None and Path(titles_path).exists():
            remote_titles_path = remote_dir + "/session_titles.txt"
            try:
                self.runner.run(command_prefix + ["push", str(titles_path), remote_titles_path])
                transferred_files.append({"local_path": str(titles_path), "remote_path": remote_titles_path})
            except Exception as exc:
                warnings.append("Không thể gửi file tiêu đề sang điện thoại: %s" % exc)

        for index, video_path in enumerate(video_paths, start=1):
            candidate = Path(video_path)
            if not candidate.exists():
                warnings.append("Bỏ qua video không tồn tại: %s" % candidate)
                continue
            remote_name = "%03d_final_video.mp4" % index
            remote_path = remote_dir + "/" + remote_name
            try:
                self.runner.run(command_prefix + ["push", str(candidate), remote_path])
                transferred_files.append({"local_path": str(candidate), "remote_path": remote_path})
            except Exception as exc:
                warnings.append("Không thể gửi video #%03d: %s" % (index, exc))

        return {
            "attempted": True,
            "pushed_count": len([item for item in transferred_files if item["remote_path"].endswith(".mp4")]),
            "device_serial": selected_serial,
            "remote_dir": remote_dir,
            "warnings": warnings,
            "transferred_files": transferred_files,
        }

    def _list_connected_devices(self) -> Tuple[List[str], Optional[str]]:
        try:
            completed = self.runner.run([self.config.adb_bin, "devices"], capture_output=True)
        except Exception as exc:
            return [], "ADB chưa sẵn sàng: %s" % exc

        device_serials = []
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            if "\tdevice" in line:
                device_serials.append(line.split("\t", 1)[0].strip())
        return device_serials, None

    def _select_device_serial(self, device_serials: List[str], preferred_serial: str = "") -> Optional[str]:
        preferred = preferred_serial.strip()
        if preferred:
            for serial in device_serials:
                if serial == preferred or serial.startswith(preferred):
                    return serial
            return None
        configured = (self.config.android_device_serial or "").strip()
        if configured:
            for serial in device_serials:
                if serial == configured or serial.startswith(configured):
                    return serial
            return None
        return device_serials[0] if device_serials else None

    def _adb_base_args(self, device_serial: Optional[str]) -> List[str]:
        args = [self.config.adb_bin]
        if device_serial:
            args.extend(["-s", device_serial])
        return args

    def _safe_remote_name(self, value: str) -> str:
        sanitized = SAFE_REMOTE_NAME_RE.sub("_", value.strip()).strip("._")
        return sanitized or "session_outputs"
