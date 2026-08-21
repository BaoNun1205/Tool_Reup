"""Telegram Bot Service & Multi-Bot Configuration View."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SmoothScrollArea,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
)

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.telegram_settings import (
    TelegramRuntimeSettings,
    load_telegram_runtime_settings,
    save_telegram_runtime_settings,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.stat_card import StatCard
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import VIDEO_CUT_MODE_LABELS, VIDEO_CUT_MODE_VALUES
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import TelegramBotMonitorThread


class TelegramView(QWidget):
    """View to manage Telegram Bot workers, tokens, and video receiving rules."""

    def __init__(
        self,
        config: PipelineConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.settings = load_telegram_runtime_settings()
        self.bot_process: subprocess.Popen | None = None
        self.monitor_thread: TelegramBotMonitorThread | None = None

        self._init_ui()
        self._load_settings()
        self.refresh_bots_table()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title & Status Card
        header_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Quản lý Telegram Bot & Nhận Video Tự Động", self)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.status_card = StatCard(FIF.ROBOT, "Trạng thái Bot", "Đã dừng", "Worker Process", self)
        self.status_card.setFixedWidth(240)
        header_layout.addWidget(self.status_card)
        main_layout.addLayout(header_layout)

        # Main Grid Layout
        grid = QGridLayout()
        grid.setSpacing(16)

        # Card 1: Runtime Service Control & Global Settings
        service_card = CardWidget(self)
        service_layout = QVBoxLayout(service_card)
        service_layout.setContentsMargins(18, 16, 18, 16)
        service_layout.setSpacing(12)
        service_layout.addWidget(SubtitleLabel("Dịch vụ Bot Worker", service_card))

        form = QFormLayout()
        form.setSpacing(10)

        self.chat_id_edit = LineEdit(service_card)
        self.chat_id_edit.setPlaceholderText("ID nhóm hoặc người nhận kết quả (Chat ID)")
        form.addRow(BodyLabel("Delivery Chat ID:", service_card), self.chat_id_edit)

        self.chk_send_result = CheckBox("Gửi video đã render về lại Telegram", service_card)
        form.addRow(self.chk_send_result)

        self.chk_save_profile = CheckBox("Tự động lưu video nhận được vào Profile tương ứng", service_card)
        form.addRow(self.chk_save_profile)

        self.combo_cut_mode = ComboBox(service_card)
        self.combo_cut_mode.addItems(list(VIDEO_CUT_MODE_LABELS.values()))
        form.addRow(BodyLabel("Chế độ xử lý video:", service_card), self.combo_cut_mode)

        self.spin_chunk_duration = DoubleSpinBox(service_card)
        self.spin_chunk_duration.setRange(0.5, 30.0)
        self.spin_chunk_duration.setSingleStep(0.1)
        self.spin_chunk_duration.setValue(2.0)
        self.spin_chunk_duration.setSuffix(" giây")
        form.addRow(BodyLabel("Thời lượng cắt cố định:", service_card), self.spin_chunk_duration)

        self.spin_scene_threshold = DoubleSpinBox(service_card)
        self.spin_scene_threshold.setRange(0.05, 0.95)
        self.spin_scene_threshold.setSingleStep(0.05)
        self.spin_scene_threshold.setValue(0.35)
        form.addRow(BodyLabel("Ngưỡng đổi cảnh (Scene):", service_card), self.spin_scene_threshold)

        service_layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.save_settings_btn = PushButton("Lưu cài đặt", service_card, FIF.SAVE)
        self.save_settings_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(self.save_settings_btn)

        self.start_bot_btn = PrimaryPushButton("Khởi động Bot", service_card, FIF.PLAY)
        self.start_bot_btn.clicked.connect(self._on_start_bot)
        btn_row.addWidget(self.start_bot_btn)

        self.stop_bot_btn = PushButton("Dừng Bot", service_card, FIF.PAUSE)
        self.stop_bot_btn.clicked.connect(self._on_stop_bot)
        btn_row.addWidget(self.stop_bot_btn)

        service_layout.addLayout(btn_row)
        service_layout.addStretch(1)
        grid.addWidget(service_card, 0, 0)

        # Card 2: Multi-Bot Configuration (telegram_bots.json)
        bots_card = CardWidget(self)
        bots_layout = QVBoxLayout(bots_card)
        bots_layout.setContentsMargins(18, 16, 18, 16)
        bots_layout.setSpacing(12)

        bots_header = QHBoxLayout()
        bots_header.addWidget(SubtitleLabel("Danh sách Bots Đa Kênh (telegram_bots.json)", bots_card))
        bots_header.addStretch(1)

        self.add_bot_btn = PrimaryPushButton("Thêm Bot", bots_card, FIF.ADD)
        self.add_bot_btn.clicked.connect(self._on_add_bot)
        bots_header.addWidget(self.add_bot_btn)

        self.reload_bots_btn = PushButton("Đọc lại file", bots_card, FIF.SYNC)
        self.reload_bots_btn.clicked.connect(self.refresh_bots_table)
        bots_header.addWidget(self.reload_bots_btn)

        bots_layout.addLayout(bots_header)

        self.bots_table = TableWidget(bots_card)
        self.bots_table.setColumnCount(3)
        self.bots_table.setHorizontalHeaderLabels(["Tên Bot", "Bot Token", "Chat ID"])
        self.bots_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.bots_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.bots_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.bots_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.bots_table.setColumnWidth(0, 160)
        self.bots_table.setColumnWidth(2, 140)
        self.bots_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bots_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.bots_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bots_table.setAlternatingRowColors(True)
        self.bots_table.setShowGrid(False)
        self.bots_table.verticalHeader().setDefaultSectionSize(40)

        bots_layout.addWidget(self.bots_table)
        grid.addWidget(bots_card, 0, 1)

        main_layout.addLayout(grid, 1)

    def _load_settings(self) -> None:
        s = self.settings
        self.chat_id_edit.setText(str(self.config.telegram_delivery_chat_id or s.delivery_chat_id or ""))
        self.chk_send_result.setChecked(bool(s.send_result_to_telegram))
        self.chk_save_profile.setChecked(bool(s.save_received_video_to_profile))

        cut_mode = s.video_cut_mode or "fixed"
        self.combo_cut_mode.setCurrentText(VIDEO_CUT_MODE_LABELS.get(cut_mode, "Cắt cố định"))
        self.spin_chunk_duration.setValue(float(s.fixed_chunk_duration_seconds if s.fixed_chunk_duration_seconds else 2.0))
        self.spin_scene_threshold.setValue(float(s.scene_threshold if s.scene_threshold else 0.35))

    def _save_settings(self) -> None:
        cut_mode_label = self.combo_cut_mode.currentText()
        cut_mode_val = VIDEO_CUT_MODE_VALUES.get(cut_mode_label, "fixed")
        chat_id_val = self.chat_id_edit.text().strip()

        new_settings = TelegramRuntimeSettings(
            bot_token=self.settings.bot_token,
            delivery_chat_id=chat_id_val,
            send_result_to_telegram=self.chk_send_result.isChecked(),
            save_received_video_to_profile=self.chk_save_profile.isChecked(),
            video_cut_mode=cut_mode_val,
            fixed_chunk_duration_seconds=float(self.spin_chunk_duration.value()),
            scene_threshold=float(self.spin_scene_threshold.value()),
            product_image_crop_ratio=self.settings.product_image_crop_ratio,
            product_image_motion=self.settings.product_image_motion,
        )
        self.settings = new_settings
        save_telegram_runtime_settings(new_settings)
        InfoBar.success("Đã lưu", "Đã cập nhật cài đặt Telegram Bot!", parent=self.window())

    def refresh_bots_table(self) -> None:
        file_path = Path("telegram_bots.json")
        if not file_path.exists():
            self.bots_table.setRowCount(0)
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            bots = data.get("bots", []) if isinstance(data, dict) else []

            self.bots_table.setRowCount(len(bots))
            for row, b in enumerate(bots):
                name = str(b.get("name") or "-")
                token = str(b.get("bot_token") or b.get("token") or "-")
                masked_token = token[:10] + "..." + token[-6:] if len(token) > 16 else token
                chat_id = str(b.get("chat_id") or b.get("delivery_chat_id") or "-")

                self.bots_table.setItem(row, 0, QTableWidgetItem(name))
                self.bots_table.setItem(row, 1, QTableWidgetItem(masked_token))
                self.bots_table.setItem(row, 2, QTableWidgetItem(chat_id))
        except Exception as exc:
            InfoBar.error("Lỗi đọc telegram_bots.json", str(exc), parent=self.window())

    def _on_add_bot(self) -> None:
        InfoBar.info("Cấu hình Bot", "Bạn có thể chỉnh sửa trực tiếp file telegram_bots.json", parent=self.window())

    def _on_start_bot(self) -> None:
        if self.bot_process and self.bot_process.poll() is None:
            InfoBar.warning("Bot đang chạy", "Dịch vụ Telegram Bot đã được khởi động!", parent=self.window())
            return

        self._save_settings()
        try:
            bots_file = Path("telegram_bots.json")
            cmd = [
                sys.executable,
                "-m",
                "auto_tiktok_editor.cli",
                "telegram-bots" if bots_file.exists() else "telegram-bot",
            ]

            self.bot_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            self.monitor_thread = TelegramBotMonitorThread(self.bot_process, self)
            self.monitor_thread.status_changed.connect(self._on_bot_status_changed)
            self.monitor_thread.start()

            self.status_card.set_value("Đang hoạt động")
            InfoBar.success("Đã bật", "Đã khởi động tiến trình Telegram Bot.", parent=self.window())
        except Exception as exc:
            self.status_card.set_value("Lỗi khởi động")
            InfoBar.error("Lỗi khởi động Bot", str(exc), parent=self.window())

    def _on_stop_bot(self) -> None:
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
        if self.bot_process:
            try:
                self.bot_process.terminate()
            except Exception:
                pass
            self.bot_process = None
        self.status_card.set_value("Đã dừng")
        InfoBar.info("Đã dừng", "Đã tắt dịch vụ Telegram Bot.", parent=self.window())

    def _on_bot_status_changed(self, is_running: bool, message: str) -> None:
        if is_running:
            self.status_card.set_value("Đang chạy")
        else:
            self.status_card.set_value("Đã dừng")

    def apply_theme_mode(self, mode: str) -> None:
        clean = "dark" if str(mode).strip().lower() == "dark" else "light"
        if hasattr(self, "status_card"):
            self.status_card.set_theme_mode(clean)

    def closeEvent(self, event) -> None:
        self._on_stop_bot()
        super().closeEvent(event)
