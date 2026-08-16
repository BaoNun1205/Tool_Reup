"""Optional uiautomator2 helpers for faster Android UI control."""

from __future__ import annotations

import time
from typing import Any, Iterable

try:
    import uiautomator2 as u2
except Exception as _uiautomator_import_error:
    u2 = None
else:
    _uiautomator_import_error = None


class UiAutomatorUnavailable(RuntimeError):
    """Raised when uiautomator2 cannot be used for the current device."""


class UiAutomatorClient:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        if u2 is None:
            raise UiAutomatorUnavailable("uiautomator2 is not installed.") from _uiautomator_import_error
        try:
            self.device = u2.connect(serial)
        except Exception as exc:
            raise UiAutomatorUnavailable("Could not connect uiautomator2 to %s." % serial) from exc

    def click_text(self, labels: Iterable[str], *, timeout: float = 2.0) -> tuple[int, int] | None:
        deadline = time.monotonic() + max(0.1, timeout)
        normalized_labels = [str(label or "").strip() for label in labels if str(label or "").strip()]
        while time.monotonic() < deadline:
            for label in normalized_labels:
                for selector in ({"text": label}, {"description": label}):
                    center = self._click_selector(selector, timeout=min(0.2, max(0.05, deadline - time.monotonic())))
                    if center is not None:
                        return center
            time.sleep(0.1)
        return None

    def click_center(self, x: int, y: int) -> tuple[int, int]:
        self.device.click(int(x), int(y))
        return int(x), int(y)

    def click_ratio(self, x_ratio: float, y_ratio: float) -> tuple[int, int]:
        width, height = self.window_size()
        return self.click_center(max(1, int(width * x_ratio)), max(1, int(height * y_ratio)))

    def window_size(self) -> tuple[int, int]:
        size = self.device.window_size()
        if isinstance(size, tuple) and len(size) >= 2:
            return int(size[0]), int(size[1])
        if isinstance(size, dict):
            return int(size.get("width") or 1080), int(size.get("height") or 2400)
        return 1080, 2400

    def dump_hierarchy(self) -> str:
        return str(self.device.dump_hierarchy() or "")

    def set_clipboard(self, text: str) -> None:
        clean_text = str(text or "")
        self.device.set_clipboard(clean_text)
        try:
            current_text = str(self.device.clipboard or "")
        except Exception as exc:
            raise UiAutomatorUnavailable("Could not verify Android clipboard.") from exc
        if current_text != clean_text:
            raise UiAutomatorUnavailable("Android clipboard did not match the requested text.")

    def get_clipboard(self) -> str:
        return str(self.device.clipboard or "")

    def has_text(self, text: str, *, timeout: float = 0.5) -> bool:
        clean_text = str(text or "").strip()
        if not clean_text:
            return False
        try:
            if self.device(text=clean_text).wait(timeout=timeout):
                return True
        except Exception:
            pass
        return clean_text in self.dump_hierarchy()

    def _click_selector(self, selector: dict[str, str], *, timeout: float) -> tuple[int, int] | None:
        try:
            element = self.device(**selector)
            if not element.wait(timeout=timeout):
                return None
            center = self._center_from_info(element.info)
            element.click()
            return center
        except Exception:
            return None

    @staticmethod
    def _center_from_info(info: Any) -> tuple[int, int]:
        bounds = {}
        if isinstance(info, dict):
            bounds = info.get("bounds") or {}
        left = int(bounds.get("left") or 0)
        top = int(bounds.get("top") or 0)
        right = int(bounds.get("right") or left)
        bottom = int(bounds.get("bottom") or top)
        return (left + right) // 2, (top + bottom) // 2
