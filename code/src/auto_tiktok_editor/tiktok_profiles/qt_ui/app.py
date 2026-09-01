from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings, QTimer, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    FluentWindow,
    IndeterminateProgressRing,
    NavigationItemPosition,
    NavigationDisplayMode,
    SubtitleLabel,
    Theme,
    setTheme,
    setThemeColor,
)

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    MODERN_DARK_STYLESHEET,
    MODERN_LIGHT_STYLESHEET,
    ModernPhoneIcon,
    get_current_theme_mode,
    set_current_theme_mode,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import (
    BrowserWorkerThread,
    LogBridgeSignals,
    QtLogHandler,
)
from auto_tiktok_editor.utils.processes import terminate_child_process_trees

APP_USER_MODEL_ID = "ToolReup.TikTokProfileManager.Live"
STARTUP_WINDOW_TITLE = "TikTok Profile Manager - Đang khởi động"


def _set_windows_app_identity() -> None:
    """Make the live-source window match its pinned taskbar shortcut."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


class StartupWindow(QWidget):
    """Small immediately-visible window while the main UI is being built."""

    def __init__(self, icon: QIcon | None = None) -> None:
        super().__init__(None)
        self.setWindowTitle(STARTUP_WINDOW_TITLE)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(460, 230)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress_ring = IndeterminateProgressRing(self, start=True)
        self.progress_ring.setFixedSize(48, 48)
        layout.addWidget(self.progress_ring, alignment=Qt.AlignmentFlag.AlignCenter)

        title = SubtitleLabel("TikTok Profile Manager", self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.status_label = BodyLabel("Đang khởi động ứng dụng...", self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        dark = get_current_theme_mode() == "dark"
        self.setStyleSheet(
            "StartupWindow { background: %s; color: %s; }"
            " BodyLabel { color: %s; }"
            % (
                "#171922" if dark else "#FFFFFF",
                "#F3F4F8" if dark else "#181B2A",
                "#B5B9C7" if dark else "#5F6475",
            )
        )

    def show_ready(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(geometry.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.processEvents()

    def update_status(self, message: str) -> None:
        self.status_label.setText(str(message))
        QApplication.processEvents()

    def finish(self, window: QWidget) -> None:
        self.progress_ring.stop()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        window.raise_()
        window.activateWindow()
        self.close()


class TikTokProfileManagerApp(FluentWindow):
    """Main application window using Windows 11 Fluent Design with native multi-monitor DPI scaling."""

    def __init__(
        self,
        manager: TikTokProfileManager | None = None,
        config: PipelineConfig | None = None,
        startup_progress=None,
    ) -> None:
        super().__init__()
        self._startup_progress = startup_progress
        self._report_startup("Đang mở dữ liệu ứng dụng...")
        self.config = config or PipelineConfig.from_env()
        self.manager = manager or TikTokProfileManager()
        self._shutdown_started = False
        self._report_startup("Đang khởi tạo dịch vụ nền...")
        self.browser_worker = BrowserWorkerThread(self.manager, channel="chrome", parent=self)
        self.browser_worker.start()

        self._report_startup("Đang dựng cửa sổ chính...")
        self._init_window()
        self._init_sub_interfaces()
        self._report_startup("Đang hoàn thiện giao diện...")
        self._init_logging_bridge()
        self._init_navigation()

        # Propagate initial theme mode to all created sub-interfaces
        initial_mode = get_current_theme_mode()
        self.apply_theme_mode(initial_mode, initial=False)
        self._report_startup("Sẵn sàng")
        self._startup_progress = None

    def _report_startup(self, message: str) -> None:
        if self._startup_progress is not None:
            self._startup_progress(message)

    def _init_window(self) -> None:
        self.setWindowTitle("TikTok Profile Manager Pro")
        self.resize(1200, 780)
        self.setMinimumSize(850, 520)

        # Apply saved or default theme mode
        initial_mode = get_current_theme_mode()
        self.apply_theme_mode(initial_mode, initial=True)

    def apply_theme_mode(self, mode: str, initial: bool = False) -> None:
        """Dynamically apply Light or Dark theme across the entire application."""
        clean_mode = "dark" if str(mode).strip().lower() == "dark" else "light"
        set_current_theme_mode(clean_mode)

        if clean_mode == "dark":
            setTheme(Theme.DARK)
            setThemeColor("#8B7CFF")
            self.setStyleSheet(MODERN_DARK_STYLESHEET)
        else:
            setTheme(Theme.LIGHT)
            setThemeColor("#6D5DFB")
            self.setStyleSheet(MODERN_LIGHT_STYLESHEET)

        if not initial:
            # Propagate theme updates to subviews
            if hasattr(self, "dashboard_view"):
                self.dashboard_view.apply_theme_mode(clean_mode)
            if hasattr(self, "accounts_view"):
                self.accounts_view.apply_theme_mode(clean_mode)
            if hasattr(self, "sources_view"):
                self.sources_view.apply_theme_mode(clean_mode)
            if hasattr(self, "videos_view"):
                self.videos_view.apply_theme_mode(clean_mode)
            if hasattr(self, "fashion_view"):
                self.fashion_view.apply_theme_mode(clean_mode)
            if hasattr(self, "phone_view"):
                self.phone_view.apply_theme_mode(clean_mode)
            if hasattr(self, "telegram_view"):
                self.telegram_view.apply_theme_mode(clean_mode)
            if hasattr(self, "logs_view"):
                self.logs_view.apply_theme_mode(clean_mode)
            if hasattr(self, "settings_view"):
                self.settings_view.sync_theme_selection(clean_mode)

    def _init_sub_interfaces(self) -> None:
        # Views
        self._report_startup("Đang tải điều khiển điện thoại...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.phone_view import PhoneControlView

        self.phone_view = PhoneControlView(self.config, self)
        self.phone_view.setObjectName("phoneInterface")

        self._report_startup("Đang tải Telegram Bot...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.telegram_view import TelegramView

        self.telegram_view = TelegramView(self.config, self)
        self.telegram_view.setObjectName("telegramInterface")

        self._report_startup("Đang tải Dashboard...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.dashboard_view import DashboardView

        self.dashboard_view = DashboardView(self.manager, self.config, self.phone_view, self.telegram_view, self)
        self.dashboard_view.setObjectName("dashboardInterface")

        self._report_startup("Đang tải Profiles...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.accounts_view import AccountsView

        self.accounts_view = AccountsView(self.manager, self.browser_worker, self)
        self.accounts_view.setObjectName("accountsInterface")

        self._report_startup("Đang tải nguồn video...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.sources_view import SourcesView

        self.sources_view = SourcesView(self.manager, self.config, self)
        self.sources_view.setObjectName("sourcesInterface")

        self._report_startup("Đang tải thư viện video...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.videos_view import VideosView

        self.videos_view = VideosView(self.manager, self.config, self)
        self.videos_view.setObjectName("videosInterface")

        self._report_startup("Đang tải Fashion...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.fashion_view import FashionView

        self.fashion_view = FashionView(self.manager, self.config, self)
        self.fashion_view.setObjectName("fashionInterface")

        self._report_startup("Đang tải nhật ký...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.log_view import LogView

        self.logs_view = LogView(self.manager, self)
        self.logs_view.setObjectName("logsInterface")

        self._report_startup("Đang tải cài đặt...")
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.settings_view import SettingsView

        self.settings_view = SettingsView(self.config, self)
        self.settings_view.setObjectName("settingsInterface")

        # Connect inter-view signals
        self.dashboard_view.request_navigate.connect(self._on_dashboard_navigate)
        self.accounts_view.request_videos_view.connect(self._on_request_videos_view)
        self.accounts_view.request_sources_view.connect(self._on_request_sources_view)

    def _init_logging_bridge(self) -> None:
        self.log_signals = LogBridgeSignals()
        self.log_signals.log_record.connect(self.logs_view.append_log)

        # Attach Qt handler to root / app loggers
        handler = QtLogHandler(self.log_signals)
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logging.getLogger("auto_tiktok_editor").addHandler(handler)

    def _init_navigation(self) -> None:
        self.navigationInterface.setExpandWidth(190)

        self.addSubInterface(
            self.dashboard_view,
            FIF.HOME,
            "Dashboard",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.accounts_view,
            FIF.PEOPLE,
            "Profiles",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.sources_view,
            FIF.GLOBE,
            "Sources",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.videos_view,
            FIF.VIDEO,
            "Videos",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.fashion_view,
            FIF.PALETTE,
            "Fashion",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.phone_view,
            ModernPhoneIcon(),
            "Phone Control",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.telegram_view,
            FIF.ROBOT,
            "Telegram Bot",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.logs_view,
            FIF.COMMAND_PROMPT,
            "Logs",
            NavigationItemPosition.TOP,
        )

        # Bottom navigation item
        self.addSubInterface(
            self.settings_view,
            FIF.SETTING,
            "Settings",
            NavigationItemPosition.BOTTOM,
        )
        self.navigationInterface.displayModeChanged.connect(self._save_navigation_display_mode)
        QTimer.singleShot(0, self._restore_navigation_display_mode)

    def _navigation_settings(self) -> QSettings:
        return QSettings("AutoTikTokEditor", "TikTokProfileManager")

    def _save_navigation_display_mode(self, display_mode) -> None:
        is_expanded = display_mode in {
            NavigationDisplayMode.EXPAND,
            NavigationDisplayMode.MENU,
        }
        self._navigation_settings().setValue("ui/navigation_expanded", is_expanded)

    def _restore_navigation_display_mode(self) -> None:
        is_expanded = self._navigation_settings().value("ui/navigation_expanded", False, type=bool)
        if is_expanded:
            self.navigationInterface.expand(useAni=False)

    def _on_dashboard_navigate(self, target: str) -> None:
        target_clean = str(target or "").strip().lower()
        if target_clean == "profiles":
            self.switchTo(self.accounts_view)
        elif target_clean == "sources":
            self.switchTo(self.sources_view)
        elif target_clean == "videos":
            self.switchTo(self.videos_view)
        elif target_clean == "phone":
            self.switchTo(self.phone_view)
        elif target_clean == "telegram":
            self.switchTo(self.telegram_view)
        elif target_clean == "logs":
            self.switchTo(self.logs_view)
        elif target_clean == "settings":
            self.switchTo(self.settings_view)

    def _on_request_videos_view(self, account) -> None:
        name = account if isinstance(account, str) else getattr(account, "name", "")
        self.videos_view.set_active_profile(name)
        self.switchTo(self.videos_view)

    def _on_request_sources_view(self, account) -> None:
        name = account if isinstance(account, str) else getattr(account, "name", "")
        for i in range(self.sources_view.profile_combo.count()):
            if self.sources_view.profile_combo.itemText(i) == name:
                self.sources_view.profile_combo.setCurrentIndex(i)
                break
        self.switchTo(self.sources_view)

    def shutdown(self) -> None:
        """Idempotently stop workers and every subprocess owned by the GUI."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        cleanup_steps = [
            ("Telegram bot", self.telegram_view.shutdown),
            ("phone control", self.phone_view.shutdown),
            ("video workers", self.videos_view.shutdown),
            ("Fashion workers", self.fashion_view.shutdown),
            ("cleanup workers", self.dashboard_view.shutdown),
            ("log polling", self.logs_view.shutdown),
            ("browser worker", self.browser_worker.stop),
        ]
        for label, cleanup in cleanup_steps:
            try:
                cleanup()
            except Exception:
                logging.getLogger("auto_tiktok_editor.shutdown").exception(
                    "Could not stop %s during application shutdown.", label
                )
        try:
            stopped = terminate_child_process_trees()
            if stopped:
                logging.getLogger("auto_tiktok_editor.shutdown").info(
                    "Stopped %s remaining child process tree(s).", stopped
                )
        except Exception:
            logging.getLogger("auto_tiktok_editor.shutdown").exception(
                "Could not stop all remaining child process trees."
            )

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)


def launch_app(
    manager: TikTokProfileManager | None = None,
    config: PipelineConfig | None = None,
) -> int:
    """Entry point for the PySide6 Fluent Application with smooth multi-monitor scaling."""
    _set_windows_app_identity()
    if sys.platform == "win32":
        try:
            # Set Per-Monitor DPI Awareness V2 (-4)
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    icon_path = Path.cwd() / "assets" / "app_icon.ico"
    icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon()
    if icon_path.is_file():
        app.setWindowIcon(icon)

    startup = StartupWindow(icon)
    startup.show_ready()
    startup.update_status("Đang nạp các thành phần giao diện...")

    window = TikTokProfileManagerApp(
        manager=manager,
        config=config,
        startup_progress=startup.update_status,
    )
    app.aboutToQuit.connect(window.shutdown)
    if icon_path.is_file():
        window.setWindowIcon(icon)
    window.showMaximized()
    QApplication.processEvents()
    startup.finish(window)
    return app.exec()
