from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    NavigationItemPosition,
    SplashScreen,
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
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.accounts_view import AccountsView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.dashboard_view import DashboardView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.log_view import LogView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.phone_view import PhoneControlView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.settings_view import SettingsView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.sources_view import SourcesView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.telegram_view import TelegramView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.videos_view import VideosView
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import (
    BrowserWorkerThread,
    LogBridgeSignals,
    QtLogHandler,
)


class TikTokProfileManagerApp(FluentWindow):
    """Main application window using Windows 11 Fluent Design with native multi-monitor DPI scaling."""

    def __init__(
        self,
        manager: TikTokProfileManager | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        super().__init__()
        self.config = config or PipelineConfig.from_env()
        self.manager = manager or TikTokProfileManager()
        self.browser_worker = BrowserWorkerThread(self.manager, channel="chrome", parent=self)
        self.browser_worker.start()

        self._init_window()
        self._init_sub_interfaces()
        self._init_logging_bridge()
        self._init_navigation()

        # Propagate initial theme mode to all created sub-interfaces
        initial_mode = get_current_theme_mode()
        self.apply_theme_mode(initial_mode, initial=False)

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
        self.phone_view = PhoneControlView(self.config, self)
        self.phone_view.setObjectName("phoneInterface")

        self.telegram_view = TelegramView(self.config, self)
        self.telegram_view.setObjectName("telegramInterface")

        self.dashboard_view = DashboardView(self.manager, self.config, self.phone_view, self.telegram_view, self)
        self.dashboard_view.setObjectName("dashboardInterface")

        self.accounts_view = AccountsView(self.manager, self.browser_worker, self)
        self.accounts_view.setObjectName("accountsInterface")

        self.sources_view = SourcesView(self.manager, self.config, self)
        self.sources_view.setObjectName("sourcesInterface")

        self.videos_view = VideosView(self.manager, self.config, self)
        self.videos_view.setObjectName("videosInterface")

        self.logs_view = LogView(self.manager, self)
        self.logs_view.setObjectName("logsInterface")

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

    def closeEvent(self, event) -> None:
        try:
            self.browser_worker.stop()
            self.phone_view.closeEvent(event)
            self.telegram_view.closeEvent(event)
        except Exception:
            pass
        super().closeEvent(event)


def launch_app(
    manager: TikTokProfileManager | None = None,
    config: PipelineConfig | None = None,
) -> int:
    """Entry point for the PySide6 Fluent Application with smooth multi-monitor scaling."""
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

    window = TikTokProfileManagerApp(manager=manager, config=config)
    window.show()
    return app.exec()

