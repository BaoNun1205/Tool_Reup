"""Phone Control & Android ADB Automation View."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SmoothScrollArea,
    SubtitleLabel,
    SwitchButton,
    ToolButton,
)

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.phone_control import (
    CLOSE_HOTKEY_LABEL,
    DEFAULT_ADB_PORT,
    PhoneControlSettings,
    PhoneController,
    SCREENSHOT_HOTKEY_LABEL,
    WindowsGlobalHotkey,
    load_phone_control_settings,
    normalize_phone_address,
    save_phone_control_settings,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.stat_card import StatCard
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import ModernPhoneIcon, TIKTOK_ANDROID_PACKAGES


class PhoneControlView(QWidget):
    """View to manage Android Phone over ADB, Scrcpy screen mirroring, and Automations."""

    def __init__(
        self,
        config: PipelineConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.phone_settings = load_phone_control_settings()
        self.phone_controller = PhoneController(self.config, on_event=self._on_phone_event)

        # Global Hotkeys
        self.screenshot_hotkey = WindowsGlobalHotkey(
            self._on_hotkey_screenshot,
            virtual_key=0x53,  # 'S' key
            thread_name="phone-screenshot-hotkey",
        )
        self.close_hotkey = WindowsGlobalHotkey(
            self._on_hotkey_close,
            virtual_key=0x51,  # 'Q' key
            thread_name="phone-close-hotkey",
        )

        self._init_ui()
        self._load_settings_to_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title
        title_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Điều khiển Điện thoại Android & Scrcpy", self)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)

        self.status_card = StatCard(ModernPhoneIcon(), "Trạng thái", "Chưa kết nối", "ADB & Scrcpy", self)
        self.status_card.setFixedWidth(240)
        title_layout.addWidget(self.status_card)
        main_layout.addLayout(title_layout)

        # Grid Content: ADB Connection & Scrcpy Settings
        content_grid = QGridLayout()
        content_grid.setSpacing(16)

        # Card 1: ADB Connection
        conn_card = CardWidget(self)
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(18, 16, 18, 16)
        conn_layout.setSpacing(12)
        conn_layout.addWidget(SubtitleLabel("Kết nối Thiết bị (ADB)", conn_card))

        conn_form = QFormLayout()
        conn_form.setSpacing(10)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(8)

        self.ip_edit = LineEdit(conn_card)
        self.ip_edit.setPlaceholderText("IP điện thoại (vd: 192.168.30.3)")
        addr_row.addWidget(self.ip_edit, 3)

        colon_lbl = BodyLabel(":", conn_card)
        colon_lbl.setStyleSheet("font-weight: bold; font-size: 15px; color: #5F6475;")
        addr_row.addWidget(colon_lbl)

        self.port_edit = LineEdit(conn_card)
        self.port_edit.setPlaceholderText("Port (vd: 5555)")
        self.port_edit.setFixedWidth(180)
        addr_row.addWidget(self.port_edit)

        conn_form.addRow(BodyLabel("Địa chỉ ADB:", conn_card), addr_row)

        conn_layout.addLayout(conn_form)

        conn_btn_row = QHBoxLayout()
        self.connect_btn = PrimaryPushButton("Kết nối ADB", conn_card, FIF.WIFI)
        self.connect_btn.clicked.connect(self._on_connect_adb)
        conn_btn_row.addWidget(self.connect_btn)

        self.disconnect_btn = PushButton("Ngắt kết nối", conn_card, FIF.CLOSE)
        self.disconnect_btn.clicked.connect(self._on_disconnect_adb)
        conn_btn_row.addWidget(self.disconnect_btn)

        self.refresh_devices_btn = PushButton("Tìm thiết bị", conn_card, FIF.SYNC)
        self.refresh_devices_btn.clicked.connect(self._on_refresh_devices)
        conn_btn_row.addWidget(self.refresh_devices_btn)
        conn_layout.addLayout(conn_btn_row)

        conn_layout.addStretch(1)
        content_grid.addWidget(conn_card, 0, 0)

        # Card 2: Scrcpy Screen Mirroring
        scrcpy_card = CardWidget(self)
        scrcpy_layout = QVBoxLayout(scrcpy_card)
        scrcpy_layout.setContentsMargins(18, 16, 18, 16)
        scrcpy_layout.setSpacing(12)
        scrcpy_layout.addWidget(SubtitleLabel("Cấu hình Màn hình Scrcpy", scrcpy_card))

        scrcpy_form = QFormLayout()
        scrcpy_form.setSpacing(8)

        self.chk_keep_awake = CheckBox("Giữ màn hình điện thoại luôn sáng", scrcpy_card)
        scrcpy_form.addRow(self.chk_keep_awake)

        self.chk_turn_off = CheckBox("Tắt màn hình điện thoại khi chiếu", scrcpy_card)
        scrcpy_form.addRow(self.chk_turn_off)

        self.chk_always_on_top = CheckBox("Cửa sổ chiếu luôn ở trên cùng (Always on top)", scrcpy_card)
        scrcpy_form.addRow(self.chk_always_on_top)

        self.combo_monitor = ComboBox(scrcpy_card)
        self.combo_monitor.addItems(["Màn hình chính (Main)", "Màn hình phụ (Secondary)"])
        scrcpy_form.addRow(BodyLabel("Vị trí hiển thị:", scrcpy_card), self.combo_monitor)

        self.combo_dock = ComboBox(scrcpy_card)
        self.combo_dock.addItems(["Tắt dock", "Dock góc trái", "Dock góc phải"])
        scrcpy_form.addRow(BodyLabel("Tự động ghim (Dock):", scrcpy_card), self.combo_dock)

        self.combo_fps = ComboBox(scrcpy_card)
        self.combo_fps.addItems(["60 FPS", "30 FPS", "120 FPS"])
        scrcpy_form.addRow(BodyLabel("Tốc độ khung hình (FPS):", scrcpy_card), self.combo_fps)

        self.combo_bitrate = ComboBox(scrcpy_card)
        self.combo_bitrate.addItems(["8 Mbps", "16 Mbps", "4 Mbps"])
        scrcpy_form.addRow(BodyLabel("Bitrate video:", scrcpy_card), self.combo_bitrate)

        scrcpy_layout.addLayout(scrcpy_form)

        scrcpy_btn_row = QHBoxLayout()
        self.start_scrcpy_btn = PrimaryPushButton("Bắt đầu chiếu Scrcpy", scrcpy_card, FIF.PLAY)
        self.start_scrcpy_btn.clicked.connect(self._on_start_scrcpy)
        scrcpy_btn_row.addWidget(self.start_scrcpy_btn)

        self.stop_scrcpy_btn = PushButton("Dừng chiếu", scrcpy_card, FIF.PAUSE)
        self.stop_scrcpy_btn.clicked.connect(self._on_stop_scrcpy)
        scrcpy_btn_row.addWidget(self.stop_scrcpy_btn)
        scrcpy_layout.addLayout(scrcpy_btn_row)

        content_grid.addWidget(scrcpy_card, 0, 1)

        # Card 3: Hotkeys & Quick Automations
        auto_card = CardWidget(self)
        auto_layout = QVBoxLayout(auto_card)
        auto_layout.setContentsMargins(18, 16, 18, 16)
        auto_layout.setSpacing(12)
        auto_layout.addWidget(SubtitleLabel("Thao tác Nhanh & Phím tắt", auto_card))

        hotkey_desc = BodyLabel(
            "• Phím 'S': Chụp nhanh màn hình điện thoại vào thư mục phone_screenshots\n"
            "• Phím 'Q': Đóng nhanh ứng dụng đang mở trên điện thoại",
            auto_card,
        )
        hotkey_desc.setStyleSheet("color: #5F6475;")
        auto_layout.addWidget(hotkey_desc)

        action_row1 = QHBoxLayout()
        self.btn_open_tiktok = PushButton("Mở TikTok App", auto_card, FIF.PLAY)
        self.btn_open_tiktok.clicked.connect(self._on_open_tiktok)
        action_row1.addWidget(self.btn_open_tiktok)

        self.btn_close_app = PushButton("Đóng App Hiện Tại", auto_card, FIF.CLOSE)
        self.btn_close_app.clicked.connect(self._on_close_current_app)
        action_row1.addWidget(self.btn_close_app)

        self.btn_clear_tiktok = PushButton("Xóa Cache TikTok", auto_card, FIF.DELETE)
        self.btn_clear_tiktok.clicked.connect(self._on_clear_tiktok_data)
        action_row1.addWidget(self.btn_clear_tiktok)
        auto_layout.addLayout(action_row1)

        action_row2 = QHBoxLayout()
        self.btn_screenshot = PushButton("Chụp màn hình (Phím S)", auto_card, FIF.CAMERA)
        self.btn_screenshot.clicked.connect(self._on_take_screenshot)
        action_row2.addWidget(self.btn_screenshot)

        self.btn_open_gallery = PushButton("Mở Bộ sưu tập ảnh", auto_card, FIF.FOLDER)
        self.btn_open_gallery.clicked.connect(self._on_open_gallery)
        action_row2.addWidget(self.btn_open_gallery)
        auto_layout.addLayout(action_row2)

        auto_layout.addStretch(1)
        content_grid.addWidget(auto_card, 1, 0, 1, 2)

        main_layout.addLayout(content_grid, 1)

    def get_adb_address(self) -> str:
        ip = self.ip_edit.text().strip()
        port = self.port_edit.text().strip()
        if not ip:
            return ""
        if ":" in ip:
            return ip
        if port:
            return f"{ip}:{port}"
        return ip

    def _load_settings_to_ui(self) -> None:
        s = self.phone_settings
        raw_addr = str(s.address or "").strip()
        if ":" in raw_addr:
            parts = raw_addr.split(":", 1)
            self.ip_edit.setText(parts[0].strip())
            self.port_edit.setText(parts[1].strip())
        else:
            self.ip_edit.setText(raw_addr)
            self.port_edit.setText("5555" if raw_addr else "")

        self.chk_keep_awake.setChecked(s.keep_screen_awake)
        self.chk_turn_off.setChecked(s.turn_screen_off)
        self.chk_always_on_top.setChecked(s.always_on_top)
        self.combo_monitor.setCurrentText("Màn hình phụ (Secondary)" if s.monitor_target == "secondary" else "Màn hình chính (Main)")

        dock_map = {"left": "Dock góc trái", "right": "Dock góc phải"}
        self.combo_dock.setCurrentText(dock_map.get(s.dock_position, "Tắt dock"))

        self.combo_fps.setCurrentText(f"{s.max_fps} FPS")
        self.combo_bitrate.setCurrentText(s.video_bit_rate.replace("M", " Mbps"))

    def _save_ui_settings(self) -> PhoneControlSettings:
        monitor_val = "secondary" if "phụ" in self.combo_monitor.currentText() else "main"
        dock_text = self.combo_dock.currentText()
        dock_val = "left" if "trái" in dock_text else ("right" if "phải" in dock_text else "off")

        fps_val = int(self.combo_fps.currentText().split()[0])
        bitrate_val = self.combo_bitrate.currentText().split()[0] + "M"

        settings = PhoneControlSettings(
            address=self.get_adb_address(),
            keep_screen_awake=self.chk_keep_awake.isChecked(),
            turn_screen_off=self.chk_turn_off.isChecked(),
            always_on_top=self.chk_always_on_top.isChecked(),
            monitor_target=monitor_val,
            dock_position=dock_val,
            max_fps=fps_val,
            max_size=1080,
            video_bit_rate=bitrate_val,
        )
        save_phone_control_settings(settings)
        self.phone_settings = settings
        return settings

    def _on_phone_event(self, event_type: str, data: str) -> None:
        pass

    def _on_hotkey_screenshot(self) -> None:
        self._on_take_screenshot()

    def _on_hotkey_close(self) -> None:
        self._on_close_current_app()

    def _on_connect_adb(self) -> None:
        settings = self._save_ui_settings()
        address = settings.address
        if not address:
            InfoBar.warning("Thiếu địa chỉ", "Vui lòng nhập IP và Port của điện thoại!", parent=self.window())
            return
        try:
            self.phone_controller.connect(address)
            self.status_card.set_value("Đã kết nối ADB")
            InfoBar.success("Thành công", f"Đã kết nối tới {address}", parent=self.window())
        except Exception as exc:
            self.status_card.set_value("Lỗi kết nối")
            InfoBar.error("Lỗi kết nối ADB", str(exc), parent=self.window())

    def _on_disconnect_adb(self) -> None:
        try:
            self.phone_controller.disconnect()
            self.status_card.set_value("Đã ngắt")
            InfoBar.info("Đã ngắt", "Đã ngắt kết nối ADB", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi", str(exc), parent=self.window())

    def _on_refresh_devices(self) -> None:
        try:
            devices = self.phone_controller.list_devices()
            if devices:
                dev = devices[0]
                if ":" in dev:
                    parts = dev.split(":", 1)
                    self.ip_edit.setText(parts[0].strip())
                    self.port_edit.setText(parts[1].strip())
                else:
                    self.ip_edit.setText(dev)
                InfoBar.success("Tìm thấy thiết bị", f"Đã phát hiện: {', '.join(devices)}", parent=self.window())
            else:
                InfoBar.warning("Không có thiết bị", "Không tìm thấy thiết bị Android nào qua ADB.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi quét thiết bị", str(exc), parent=self.window())

    def _on_start_scrcpy(self) -> None:
        settings = self._save_ui_settings()
        try:
            self.phone_controller.start_scrcpy(settings)
            self.status_card.set_value("Đang chiếu Scrcpy")
            InfoBar.success("Scrcpy", "Đang khởi động cửa sổ chiếu màn hình...", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi mở Scrcpy", str(exc), parent=self.window())

    def _on_stop_scrcpy(self) -> None:
        try:
            self.phone_controller.stop_scrcpy()
            self.status_card.set_value("Đã dừng Scrcpy")
            InfoBar.info("Scrcpy", "Đã đóng màn hình Scrcpy.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi", str(exc), parent=self.window())

    def _on_open_tiktok(self) -> None:
        try:
            self.phone_controller.open_tiktok()
            InfoBar.success("Thao tác", "Đã mở TikTok trên điện thoại.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi mở TikTok", str(exc), parent=self.window())

    def _on_close_current_app(self) -> None:
        try:
            self.phone_controller.close_current_app()
            InfoBar.info("Thao tác", "Đã đóng app đang chạy trên điện thoại.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi", str(exc), parent=self.window())

    def _on_clear_tiktok_data(self) -> None:
        try:
            self.phone_controller.clear_tiktok_cache()
            InfoBar.success("Đã xóa cache", "Đã dọn dẹp bộ nhớ đệm TikTok trên điện thoại.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi", str(exc), parent=self.window())

    def _on_take_screenshot(self) -> None:
        try:
            path = self.phone_controller.take_screenshot()
            InfoBar.success("Đã chụp", f"Đã lưu ảnh màn hình vào: {path}", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi chụp màn hình", str(exc), parent=self.window())

    def _on_open_gallery(self) -> None:
        try:
            self.phone_controller.open_gallery()
        except Exception as exc:
            InfoBar.error("Lỗi", str(exc), parent=self.window())

    def apply_theme_mode(self, mode: str) -> None:
        clean = "dark" if str(mode).strip().lower() == "dark" else "light"
        if hasattr(self, "status_card"):
            self.status_card.set_theme_mode(clean)

    def closeEvent(self, event) -> None:
        try:
            self.screenshot_hotkey.stop()
            self.close_hotkey.stop()
            self.phone_controller.cleanup()
        except Exception:
            pass
        super().closeEvent(event)
