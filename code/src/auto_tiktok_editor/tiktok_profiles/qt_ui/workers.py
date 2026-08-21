"""Qt Worker Threads, Signal Bridges, and Background Task Management."""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot


class LogBridgeSignals(QObject):
    log_record = Signal(str, str, str)  # timestamp, level, message


class QtLogHandler(logging.Handler):
    """Logging handler that emits Qt signals to be received by UI thread."""

    def __init__(self, signals: LogBridgeSignals) -> None:
        super().__init__()
        self.signals = signals

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname
            timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            self.signals.log_record.emit(timestamp, level, msg)
        except Exception:
            self.handleError(record)


class WorkerSignals(QObject):
    """Signals available from a running worker task."""
    started = Signal()
    finished = Signal(object)
    error = Signal(Exception, str)
    progress = Signal(int, str)


class WorkerThread(QThread):
    """Dedicated background QThread to execute a callable safely without C++ segfaults."""
    started_task = Signal()
    finished_task = Signal(object)
    error_task = Signal(Exception, str)
    progress_task = Signal(int, str)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: QObject | None = None, **kwargs: Any) -> None:
        super().__init__(parent)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        self.started_task.emit()
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.finished_task.emit(result)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            self.error_task.emit(exc, tb)


class PhoneEventBridge(QObject):
    """Bridge for PhoneController event callbacks to Qt Signals."""
    phone_event = Signal(str, str)
    hotkey_screenshot = Signal()
    hotkey_close = Signal()


class TelegramBotMonitorThread(QThread):
    """Monitors the Telegram bot subprocess and emits log/status signals."""
    status_changed = Signal(bool, str)  # is_running, message
    line_received = Signal(str)

    def __init__(self, process: subprocess.Popen, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = process
        self._running = True

    def run(self) -> None:
        self.status_changed.emit(True, "Telegram bot process is running.")
        try:
            if self.process.stdout:
                for line in iter(self.process.stdout.readline, ""):
                    if not self._running:
                        break
                    text = line.strip()
                    if text:
                        self.line_received.emit(text)
            self.process.wait()
        except Exception as exc:
            self.line_received.emit(f"Process monitor error: {exc}")
        finally:
            returncode = self.process.returncode if self.process else -1
            self.status_changed.emit(False, f"Telegram bot stopped (exit code {returncode}).")

    def stop(self) -> None:
        self._running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.wait(2000)


class BrowserWorkerThread(QThread):
    """Dedicated background thread for all Playwright browser operations."""
    started_task = Signal(str)
    finished_task = Signal(object, object)  # (result, callback)
    error_task = Signal(Exception, str, str)  # (exception, traceback_str, error_title)

    def __init__(
        self,
        manager: Any,
        channel: str = "chrome",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.channel = channel
        self.browser: Any = None
        self.task_queue: queue.Queue = queue.Queue()
        self._running = True

    def run(self) -> None:
        from auto_tiktok_editor.tiktok_profiles.profile_browser import TikTokProfileBrowser
        self.browser = TikTokProfileBrowser(self.manager, channel=self.channel)
        while self._running:
            item = self.task_queue.get()
            if item is None:
                if self.browser:
                    try:
                        self.browser.close_all()
                    except Exception:
                        pass
                break
            func, callback, error_title = item
            try:
                result = func(self.browser)
                self.finished_task.emit(result, callback)
            except Exception as exc:
                import traceback
                self.error_task.emit(exc, traceback.format_exc(), error_title)

    def submit(
        self,
        func: Callable[[Any], Any],
        callback: Callable[[Any], None] | None = None,
        error_title: str = "Lỗi trình duyệt",
        message: str = "",
    ) -> None:
        if message:
            self.started_task.emit(message)
        self.task_queue.put((func, callback, error_title))

    def open_tiktok_studio(
        self,
        account: Any,
        callback: Callable[[str], None] | None = None,
    ) -> None:
        from auto_tiktok_editor.tiktok_profiles.models import ACCOUNT_STATUSES

        def _job(b: Any) -> str:
            status = b.open_tiktok_studio(account)
            if status in ACCOUNT_STATUSES:
                self.manager.update_status(account.id, status)
            return status

        self.submit(
            _job,
            callback=callback,
            error_title=f"Mở trình duyệt ({account.name})",
            message=f"Đang khởi động trình duyệt cho profile {account.name}...",
        )

    def stop(self) -> None:
        self._running = False
        self.task_queue.put(None)
        self.wait(3000)

