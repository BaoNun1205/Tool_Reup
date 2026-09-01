"""Dashboard View: Overview statistics, quick phone ADB connect, quick bot start, shortcuts and recent logs."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SmoothScrollArea,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.phone_control import (
    PhoneControlSettings,
    PhoneController,
    load_phone_control_settings,
    normalize_phone_address,
    save_phone_control_settings,
)
from auto_tiktok_editor.telegram_settings import load_telegram_runtime_settings
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.stat_card import StatCard
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import WorkerThread
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    ModernPhoneIcon,
    format_vietnam_datetime,
    get_current_theme_mode,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.phone_view import PhoneControlView
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.telegram_view import TelegramView


class DashboardView(QWidget):
    """Modern Dashboard View with overview KPIs, quick phone and bot control, and recent activity."""

    request_navigate = Signal(str)  # "profiles", "sources", "videos", "phone", "telegram", "logs", "settings"

    def __init__(
        self,
        manager: TikTokProfileManager,
        config: PipelineConfig,
        phone_view: PhoneControlView | None = None,
        telegram_view: TelegramView | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.config = config
        self.phone_view = phone_view
        self.telegram_view = telegram_view
        self._cleanup_workers: list[WorkerThread] = []

        self._init_ui()
        self.refresh_dashboard()

        # Real-time refresh timer for dashboard stats (every 3 seconds)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self._sync_stats_live)
        self._refresh_timer.start()

    def _init_ui(self) -> None:
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget(scroll)
        container.setObjectName("dashboardContainer")
        container.setStyleSheet("QWidget#dashboardContainer { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # Header Title & Refresh Button
        title_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Dashboard", container)
        title_layout.addWidget(self.title_label)

        self.subtitle_label = CaptionLabel("Tổng quan hệ thống, trạng thái hoạt động & điều khiển nhanh", container)
        self.subtitle_label.setStyleSheet("color: #7F8596; margin-left: 8px;")
        title_layout.addWidget(self.subtitle_label)
        title_layout.addStretch(1)

        self.refresh_btn = PushButton("Làm mới", container, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_dashboard)
        title_layout.addWidget(self.refresh_btn)
        layout.addLayout(title_layout)

        # 1. KPI Statistics Overview (5 Cards)
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(12)

        self.card_profiles = StatCard(FIF.PEOPLE, "Profiles", "0", "0 hoạt động", container)
        self.card_sources = StatCard(FIF.GLOBE, "Sources", "0", "Kênh nguồn mẫu", container)
        self.card_videos = StatCard(FIF.VIDEO, "Tổng Videos", "0", "Trong kho dữ liệu", container)
        self.card_ready = StatCard(FIF.ACCEPT, "Sẵn sàng", "0", "Video đã tạo", container)
        self.card_scheduled = StatCard(FIF.DATE_TIME, "Đã hẹn lịch", "0", "Chờ xuất bản", container)

        kpi_grid.addWidget(self.card_profiles, 0, 0)
        kpi_grid.addWidget(self.card_sources, 0, 1)
        kpi_grid.addWidget(self.card_videos, 0, 2)
        kpi_grid.addWidget(self.card_ready, 0, 3)
        kpi_grid.addWidget(self.card_scheduled, 0, 4)
        layout.addLayout(kpi_grid)

        # 2. Middle Row: Quick Phone Control (Left) + Quick Telegram Bot (Right)
        mid_grid = QGridLayout()
        mid_grid.setSpacing(16)

        # 2.1 Card: Quick Phone Control
        phone_card = CardWidget(container)
        phone_layout = QVBoxLayout(phone_card)
        phone_layout.setContentsMargins(18, 16, 18, 16)
        phone_layout.setSpacing(12)

        phone_header = QHBoxLayout()
        phone_header.addWidget(SubtitleLabel("Điện thoại Android (ADB & Scrcpy)", phone_card))
        phone_header.addStretch(1)
        self.phone_status_badge = BodyLabel("● Chưa kết nối", phone_card)
        self.phone_status_badge.setStyleSheet("color: #E24A4A; font-weight: bold;")
        phone_header.addWidget(self.phone_status_badge)
        phone_layout.addLayout(phone_header)

        phone_form = QFormLayout()
        phone_form.setSpacing(8)

        self.phone_connection_mode_combo = ComboBox(phone_card)
        self.phone_connection_mode_combo.addItem("Wi-Fi / IP (mặc định)", userData="wifi")
        self.phone_connection_mode_combo.addItem("USB", userData="usb")
        self.phone_connection_mode_combo.currentIndexChanged.connect(self._on_dashboard_connection_mode_changed)
        phone_form.addRow(BodyLabel("Chế độ kết nối:", phone_card), self.phone_connection_mode_combo)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(6)
        self.phone_ip_edit = LineEdit(phone_card)
        self.phone_ip_edit.setPlaceholderText("IP điện thoại (vd: 192.168.1.10)")
        addr_row.addWidget(self.phone_ip_edit, 3)

        self.colon_lbl = BodyLabel(":", phone_card)
        self.colon_lbl.setStyleSheet("font-weight: bold; color: #5F6475;")
        addr_row.addWidget(self.colon_lbl)

        self.phone_port_edit = LineEdit(phone_card)
        self.phone_port_edit.setPlaceholderText("Port (5555)")
        self.phone_port_edit.setFixedWidth(110)
        addr_row.addWidget(self.phone_port_edit, 1)

        phone_form.addRow(BodyLabel("Địa chỉ ADB:", phone_card), addr_row)
        phone_layout.addLayout(phone_form)

        phone_btn_row = QHBoxLayout()
        phone_btn_row.setSpacing(8)

        self.btn_phone_connect = PrimaryPushButton("Kết nối ADB", phone_card, FIF.WIFI)
        self.btn_phone_connect.clicked.connect(self._on_quick_connect_phone)
        phone_btn_row.addWidget(self.btn_phone_connect)

        self.btn_phone_scrcpy = PushButton("Mở Scrcpy", phone_card, FIF.PHONE)
        self.btn_phone_scrcpy.clicked.connect(self._on_quick_open_scrcpy)
        phone_btn_row.addWidget(self.btn_phone_scrcpy)

        self.btn_phone_disconnect = PushButton("Ngắt kết nối", phone_card, FIF.CLOSE)
        self.btn_phone_disconnect.clicked.connect(self._on_quick_disconnect_phone)
        phone_btn_row.addWidget(self.btn_phone_disconnect)

        self.btn_goto_phone = PushButton("Chi tiết", phone_card, FIF.CHEVRON_RIGHT)
        self.btn_goto_phone.clicked.connect(lambda: self.request_navigate.emit("phone"))
        phone_btn_row.addWidget(self.btn_goto_phone)

        phone_layout.addLayout(phone_btn_row)
        phone_layout.addStretch(1)
        mid_grid.addWidget(phone_card, 0, 0)

        # 2.2 Card: Quick Telegram Bot
        bot_card = CardWidget(container)
        bot_layout = QVBoxLayout(bot_card)
        bot_layout.setContentsMargins(18, 16, 18, 16)
        bot_layout.setSpacing(12)

        bot_header = QHBoxLayout()
        bot_header.addWidget(SubtitleLabel("Dịch vụ Telegram Bot", bot_card))
        bot_header.addStretch(1)
        self.bot_status_badge = BodyLabel("● Đã dừng", bot_card)
        self.bot_status_badge.setStyleSheet("color: #8C8C8C; font-weight: bold;")
        bot_header.addWidget(self.bot_status_badge)
        bot_layout.addLayout(bot_header)

        bot_info_form = QFormLayout()
        bot_info_form.setSpacing(8)

        self.bot_chat_id_lbl = BodyLabel("Chưa đặt", bot_card)
        self.bot_chat_id_lbl.setStyleSheet("color: #5F6475; font-weight: bold;")
        bot_info_form.addRow(BodyLabel("Delivery Chat ID:", bot_card), self.bot_chat_id_lbl)

        self.bot_mode_lbl = BodyLabel("Cắt cố định (2.0s)", bot_card)
        self.bot_mode_lbl.setStyleSheet("color: #5F6475;")
        bot_info_form.addRow(BodyLabel("Chế độ xử lý:", bot_card), self.bot_mode_lbl)

        bot_layout.addLayout(bot_info_form)

        bot_btn_row = QHBoxLayout()
        bot_btn_row.setSpacing(8)

        self.btn_bot_start = PrimaryPushButton("Khởi động Bot", bot_card, FIF.PLAY)
        self.btn_bot_start.clicked.connect(self._on_quick_start_bot)
        bot_btn_row.addWidget(self.btn_bot_start)

        self.btn_bot_stop = PushButton("Dừng Bot", bot_card, FIF.PAUSE)
        self.btn_bot_stop.clicked.connect(self._on_quick_stop_bot)
        bot_btn_row.addWidget(self.btn_bot_stop)

        self.btn_goto_bot = PushButton("Cài đặt Bot", bot_card, FIF.SETTING)
        self.btn_goto_bot.clicked.connect(lambda: self.request_navigate.emit("telegram"))
        bot_btn_row.addWidget(self.btn_goto_bot)

        bot_layout.addLayout(bot_btn_row)
        bot_layout.addStretch(1)
        mid_grid.addWidget(bot_card, 0, 1)

        layout.addLayout(mid_grid)

        # 3. Quick Action Shortcuts Row
        actions_card = CardWidget(container)
        actions_layout = QHBoxLayout(actions_card)
        actions_layout.setContentsMargins(18, 12, 18, 12)
        actions_layout.setSpacing(12)

        actions_layout.addWidget(SubtitleLabel("Lối tắt nhanh:", actions_card))

        btn_add_vid = PrimaryPushButton("Thêm Video", actions_card, FIF.VIDEO)
        btn_add_vid.clicked.connect(lambda: self.request_navigate.emit("videos"))
        actions_layout.addWidget(btn_add_vid)

        btn_add_prof = PushButton("Quản lý Profiles", actions_card, FIF.PEOPLE)
        btn_add_prof.clicked.connect(lambda: self.request_navigate.emit("profiles"))
        actions_layout.addWidget(btn_add_prof)

        btn_add_src = PushButton("Quản lý Sources", actions_card, FIF.GLOBE)
        btn_add_src.clicked.connect(lambda: self.request_navigate.emit("sources"))
        actions_layout.addWidget(btn_add_src)

        btn_open_out = PushButton("Mở thư mục Profiles", actions_card, FIF.FOLDER)
        btn_open_out.clicked.connect(self._on_open_profiles_folder)
        actions_layout.addWidget(btn_open_out)

        btn_cleanup_sys = PushButton("Dọn dẹp Hệ thống", actions_card, FIF.DELETE)
        btn_cleanup_sys.clicked.connect(self._on_open_cleanup_dialog)
        actions_layout.addWidget(btn_cleanup_sys)

        btn_view_logs = PushButton("Xem Toàn bộ Logs", actions_card, FIF.COMMAND_PROMPT)
        btn_view_logs.clicked.connect(lambda: self.request_navigate.emit("logs"))
        actions_layout.addWidget(btn_view_logs)

        actions_layout.addStretch(1)
        layout.addWidget(actions_card)

        # 4. Recent Activity Logs Table
        logs_card = CardWidget(container)
        logs_layout = QVBoxLayout(logs_card)
        logs_layout.setContentsMargins(18, 16, 18, 16)
        logs_layout.setSpacing(10)

        logs_header = QHBoxLayout()
        logs_header.addWidget(SubtitleLabel("Nhật ký hoạt động gần đây (Recent Activity)", logs_card))
        logs_header.addStretch(1)
        btn_refresh_logs = ToolButton(FIF.SYNC, logs_card)
        btn_refresh_logs.setToolTip("Làm mới nhật ký")
        btn_refresh_logs.clicked.connect(self._refresh_recent_logs)
        logs_header.addWidget(btn_refresh_logs)
        logs_layout.addLayout(logs_header)

        self.logs_table = TableWidget(logs_card)
        self.logs_table.setColumnCount(4)
        self.logs_table.setHorizontalHeaderLabels([
            "Thời gian",
            "Mức độ",
            "Hành động",
            "Chi tiết nội dung",
        ])
        self.logs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.logs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.logs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.logs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.logs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.logs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.logs_table.setAlternatingRowColors(True)
        self.logs_table.setShowGrid(False)
        self.logs_table.verticalHeader().setDefaultSectionSize(36)
        self.logs_table.setMinimumHeight(240)

        self.logs_table.setColumnWidth(0, 150)
        self.logs_table.setColumnWidth(1, 90)
        self.logs_table.setColumnWidth(2, 180)

        logs_layout.addWidget(self.logs_table)
        layout.addWidget(logs_card)

        layout.addStretch(1)
        scroll.setWidget(container)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(scroll)

        # Pre-fill phone settings
        self._load_saved_phone_settings()
        from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode
        self.apply_theme_mode(get_current_theme_mode())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_dashboard()
        if hasattr(self, "_refresh_timer") and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _load_saved_phone_settings(self) -> None:
        try:
            settings = load_phone_control_settings()
            mode_index = self.phone_connection_mode_combo.findData(
                getattr(settings, "connection_mode", "wifi")
            )
            self.phone_connection_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            addr = str(settings.address or "").strip()
            if ":" in addr:
                parts = addr.split(":", 1)
                self.phone_ip_edit.setText(parts[0].strip())
                self.phone_port_edit.setText(parts[1].strip())
            else:
                self.phone_ip_edit.setText(addr)
                self.phone_port_edit.setText("5555" if addr else "")
            self._on_dashboard_connection_mode_changed(self.phone_connection_mode_combo.currentIndex())
        except Exception:
            pass

    def _dashboard_connection_mode(self) -> str:
        return str(self.phone_connection_mode_combo.currentData() or "wifi")

    def _on_dashboard_connection_mode_changed(self, _index: int) -> None:
        is_wifi = self._dashboard_connection_mode() == "wifi"
        self.phone_ip_edit.setEnabled(is_wifi)
        self.phone_port_edit.setEnabled(is_wifi)
        if hasattr(self, "btn_phone_connect"):
            self.btn_phone_connect.setText(
                "Kết nối Wi-Fi" if is_wifi else "Kết nối USB"
            )

    def refresh_dashboard(self) -> None:
        """Full refresh of statistics, phone connection status, bot status, and recent logs."""
        self._sync_stats_live()
        self._refresh_recent_logs()

    def _sync_stats_live(self) -> None:
        """Lightweight live sync of stats, phone status, and telegram bot status."""
        try:
            # 1. Accounts
            accounts = self.manager.list_accounts()
            total_accounts = len(accounts)
            active_accounts = len([a for a in accounts if str(getattr(a, "status", "")).lower() == "active"])
            self.card_profiles.set_value(str(total_accounts))
            self.card_profiles.set_description(f"{active_accounts} profile hoạt động")

            # 2. Sources
            sources = self.manager.list_source_channels()
            self.card_sources.set_value(str(len(sources)))
            featured_sources = len([s for s in sources if getattr(s, "is_featured", False)])
            self.card_sources.set_description(f"{featured_sources} kênh ưu tiên" if featured_sources else "Kênh nguồn mẫu")

            # 3. Videos
            videos = self.manager.list_videos()
            total_videos = len(videos)
            ready_videos = len([v for v in videos if str(getattr(v, "status", "")).lower() in ("ready", "published", "prepared")])
            scheduled_videos = len([v for v in videos if str(getattr(v, "status", "")).lower() == "scheduled" or getattr(v, "scheduled_at", None)])

            self.card_videos.set_value(str(total_videos))
            self.card_videos.set_description(f"{total_videos} video trong kho")

            self.card_ready.set_value(str(ready_videos))
            self.card_ready.set_description("Đã tạo hoàn chỉnh")

            self.card_scheduled.set_value(str(scheduled_videos))
            self.card_scheduled.set_description("Đã đặt lịch xuất bản")

            # 4. Sync Phone Status
            self._update_phone_status_ui()

            # 5. Sync Telegram Bot Status
            self._update_bot_status_ui()

        except Exception:
            pass

    def _update_phone_status_ui(self) -> None:
        try:
            controller = (
                self.phone_view.phone_controller
                if (self.phone_view and hasattr(self.phone_view, "phone_controller"))
                else PhoneController(self.config)
            )
            devices = controller.list_devices()
            if devices:
                dev_name = devices[0]
                self.phone_status_badge.setText(f"● Đã kết nối: {dev_name}")
                self.phone_status_badge.setStyleSheet("color: #2ECC71; font-weight: bold;")
            else:
                self.phone_status_badge.setText("● Chưa kết nối ADB")
                self.phone_status_badge.setStyleSheet("color: #E24A4A; font-weight: bold;")
        except Exception:
            self.phone_status_badge.setText("● Chưa kết nối")
            self.phone_status_badge.setStyleSheet("color: #8C8C8C; font-weight: bold;")

    def _update_bot_status_ui(self) -> None:
        # Load latest Telegram settings info
        try:
            tg_settings = load_telegram_runtime_settings()
            chat_id_val = str(getattr(tg_settings, "delivery_chat_id", "") or "").strip()
            self.bot_chat_id_lbl.setText(chat_id_val if chat_id_val else "Chưa cấu hình")

            cut_mode_val = str(getattr(tg_settings, "video_cut_mode", "fixed") or "fixed")
            dur_val = getattr(tg_settings, "fixed_chunk_duration", 2.0)
            if cut_mode_val == "fixed":
                self.bot_mode_lbl.setText(f"Cắt cố định ({dur_val:.1f}s)")
            elif cut_mode_val in ("scene", "smart"):
                self.bot_mode_lbl.setText("Cắt theo đổi cảnh (Scene)")
            else:
                self.bot_mode_lbl.setText("Giữ nguyên video gốc")
        except Exception:
            pass

        if self.telegram_view:
            is_running = self.telegram_view.bot_process is not None and self.telegram_view.bot_process.poll() is None
            if is_running:
                self.bot_status_badge.setText("● Đang chạy worker")
                self.bot_status_badge.setStyleSheet("color: #2ECC71; font-weight: bold;")
            else:
                self.bot_status_badge.setText("● Đã dừng")
                self.bot_status_badge.setStyleSheet("color: #8C8C8C; font-weight: bold;")

    def _refresh_recent_logs(self) -> None:
        try:
            logs = self.manager.list_logs(limit=10)
            self.logs_table.blockSignals(True)
            self.logs_table.setRowCount(len(logs))

            for row, log_item in enumerate(logs):
                # Col 0: Thời gian
                created_str = format_vietnam_datetime(getattr(log_item, "created_at", None))
                time_item = QTableWidgetItem(created_str)
                time_item.setForeground(QColor("#7F8596"))
                self.logs_table.setItem(row, 0, time_item)

                # Col 1: Mức độ
                level_str = str(getattr(log_item, "level", "info") or "info").upper()
                level_item = QTableWidgetItem(level_str)
                level_font = level_item.font()
                level_font.setBold(True)
                level_item.setFont(level_font)
                if level_str == "ERROR":
                    level_item.setForeground(QColor("#E24A4A"))
                elif level_str == "WARNING":
                    level_item.setForeground(QColor("#F59E0B"))
                else:
                    level_item.setForeground(QColor("#3B82F6"))
                self.logs_table.setItem(row, 1, level_item)

                # Col 2: Hành động
                action_str = str(getattr(log_item, "action", "") or "")
                action_item = QTableWidgetItem(action_str)
                self.logs_table.setItem(row, 2, action_item)

                # Col 3: Chi tiết
                message_str = str(getattr(log_item, "message", "") or "")
                msg_item = QTableWidgetItem(message_str)
                self.logs_table.setItem(row, 3, msg_item)

            self.logs_table.blockSignals(False)
        except Exception:
            pass

    def _on_quick_connect_phone(self) -> None:
        mode = self._dashboard_connection_mode()
        ip = self.phone_ip_edit.text().strip()
        port = self.phone_port_edit.text().strip()
        if mode == "wifi" and not ip:
            InfoBar.warning("Thiếu địa chỉ", "Vui lòng nhập IP điện thoại!", parent=self.window())
            return
        address = f"{ip}:{port}" if port and ":" not in ip else ip

        # Save to phone settings
        try:
            s = load_phone_control_settings()
            s = replace(s, address=address or s.address, connection_mode=mode)
            save_phone_control_settings(s)
            if self.phone_view:
                self.phone_view.phone_settings = s
                self.phone_view._load_settings_to_ui()
                self.phone_view._on_connect_adb()
            else:
                controller = PhoneController(self.config)
                result = controller.connect(address, connection_mode=mode)
                InfoBar.success("Thành công", f"Đã kết nối tới {result['address']}", parent=self.window())
            self._update_phone_status_ui()
        except Exception as exc:
            InfoBar.error("Lỗi kết nối ADB", str(exc), parent=self.window())

    def _on_quick_disconnect_phone(self) -> None:
        if self.phone_view:
            self.phone_view._on_disconnect_adb()
        else:
            try:
                controller = PhoneController(self.config)
                controller.disconnect()
                InfoBar.info("Đã ngắt", "Đã ngắt kết nối ADB", parent=self.window())
            except Exception as exc:
                InfoBar.error("Lỗi", str(exc), parent=self.window())
        self._update_phone_status_ui()

    def _on_quick_open_scrcpy(self) -> None:
        mode = self._dashboard_connection_mode()
        ip = self.phone_ip_edit.text().strip()
        port = self.phone_port_edit.text().strip()
        if mode == "wifi" and ip:
            address = f"{ip}:{port}" if port and ":" not in ip else ip
            try:
                s = load_phone_control_settings()
                s = replace(s, address=address, connection_mode=mode)
                save_phone_control_settings(s)
                if self.phone_view:
                    self.phone_view.phone_settings = s
                    self.phone_view._load_settings_to_ui()
            except Exception:
                pass
        elif mode == "usb":
            try:
                s = replace(load_phone_control_settings(), connection_mode="usb")
                save_phone_control_settings(s)
                if self.phone_view:
                    self.phone_view.phone_settings = s
                    self.phone_view._load_settings_to_ui()
            except Exception:
                pass

        if self.phone_view:
            self.phone_view._on_start_scrcpy()
        else:
            try:
                s = load_phone_control_settings()
                controller = PhoneController(self.config)
                controller.start_scrcpy(s)
                InfoBar.success("Scrcpy", "Đang khởi động cửa sổ chiếu màn hình...", parent=self.window())
            except Exception as exc:
                InfoBar.error("Lỗi mở Scrcpy", str(exc), parent=self.window())

    def _on_quick_start_bot(self) -> None:
        if self.telegram_view:
            self.telegram_view._on_start_bot()
            self._update_bot_status_ui()
        else:
            InfoBar.warning("Chưa sẵn sàng", "View Telegram Bot chưa khởi tạo!", parent=self.window())

    def _on_quick_stop_bot(self) -> None:
        if self.telegram_view:
            self.telegram_view._on_stop_bot()
            self._update_bot_status_ui()

    def _on_open_profiles_folder(self) -> None:
        folder = str(self.manager.profiles_root)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            InfoBar.error("Lỗi mở thư mục", str(exc), parent=self.window())

    def _on_open_cleanup_dialog(self) -> None:
        try:
            from auto_tiktok_editor.app.media_cleanup import execute_granular_cleanup, format_granular_cleanup_report
            from auto_tiktok_editor.tiktok_profiles.qt_ui.dialogs.cleanup_dialog import (
                CleanupDialog,
                CleanupProgressDialog,
            )

            phone_ctrl = getattr(self.phone_view, "phone_controller", None) if self.phone_view else None
            dialog = CleanupDialog(
                parent=self.window(),
                config=self.config,
                project_root=self.manager.project_root,
                phone_controller=phone_ctrl,
                manager=self.manager,
            )
            if dialog.exec() and dialog.confirmed:
                selected_keys = dialog.get_selected_keys()
                if not selected_keys:
                    return
                progress_dialog = CleanupProgressDialog(parent=self.window())
                outcome: dict[str, Any] = {}

                def _cleanup() -> Any:
                    return execute_granular_cleanup(
                        selected_keys=selected_keys,
                        config=self.config,
                        project_root=self.manager.project_root,
                        manager=self.manager,
                        phone_controller=phone_ctrl,
                    )

                worker = WorkerThread(_cleanup, parent=self)
                self._cleanup_workers.append(worker)

                def _finish_worker() -> None:
                    if worker in self._cleanup_workers:
                        self._cleanup_workers.remove(worker)

                def _on_cleanup_done(report: Any) -> None:
                    outcome["report"] = report
                    progress_dialog.finish()

                def _on_cleanup_error(exc: Exception, _traceback: str) -> None:
                    outcome["error"] = exc
                    progress_dialog.finish()

                worker.finished_task.connect(_on_cleanup_done)
                worker.finished_task.connect(_finish_worker)
                worker.error_task.connect(_on_cleanup_error)
                worker.error_task.connect(_finish_worker)
                worker.start()
                progress_dialog.exec()

                if "error" in outcome:
                    raise outcome["error"]
                report = outcome.get("report")
                if report is None:
                    return
                msg = format_granular_cleanup_report(report)
                if self.manager and hasattr(self.manager, "add_log"):
                    self.manager.add_log("info", "system_cleanup", msg)
                self.refresh_dashboard()
                InfoBar.success(
                    title="Đã dọn dẹp hệ thống",
                    content=msg,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self.window(),
                )
        except Exception as exc:
            InfoBar.error("Lỗi dọn dẹp", str(exc), parent=self.window())

    def apply_theme_mode(self, mode: str) -> None:
        clean = "dark" if str(mode).strip().lower() == "dark" else "light"
        for card in [self.card_profiles, self.card_sources, self.card_videos, self.card_ready, self.card_scheduled]:
            card.set_theme_mode(clean)
        self.subtitle_label.setStyleSheet("color: #B5B9C7; margin-left: 8px;" if clean == "dark" else "color: #7F8596; margin-left: 8px;")
        if hasattr(self, "colon_lbl"):
            self.colon_lbl.setStyleSheet("font-weight: bold; color: #B5B9C7;" if clean == "dark" else "font-weight: bold; color: #5F6475;")
        if hasattr(self, "bot_chat_id_lbl"):
            self.bot_chat_id_lbl.setStyleSheet("color: #B5B9C7; font-weight: bold;" if clean == "dark" else "color: #5F6475; font-weight: bold;")
        if hasattr(self, "bot_mode_lbl"):
            self.bot_mode_lbl.setStyleSheet("color: #B5B9C7;" if clean == "dark" else "color: #5F6475;")
        self._refresh_recent_logs()

    def shutdown(self) -> None:
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        workers = list(self._cleanup_workers)
        self._cleanup_workers.clear()
        for worker in workers:
            worker.stop(timeout_ms=1500)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
