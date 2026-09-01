"""System Settings & Storage Cleanup View."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PasswordLineEdit,
    PushButton,
    SingleDirectionScrollArea,
    SmoothScrollArea,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    setTheme,
    Theme,
)

from auto_tiktok_editor.app.gemini import DEFAULT_GEMINI_MODEL, GEMINI_MODELS
from auto_tiktok_editor.app.media_cleanup import execute_granular_cleanup, format_granular_cleanup_report
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.gemini_settings import (
    get_gemini_api_key,
    get_gemini_model,
    save_gemini_api_key,
    save_gemini_model,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.stat_card import StatCard
from auto_tiktok_editor.tiktok_profiles.qt_ui.dialogs.cleanup_dialog import CleanupDialog
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    PRODUCT_IMAGE_CROP_RATIO_LABELS,
    PRODUCT_IMAGE_MOTION_LABELS,
    VIDEO_CUT_MODE_LABELS,
)


class SettingsView(QWidget):
    """View for configuring video rendering pipeline and managing system storage."""

    def __init__(
        self,
        config: PipelineConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title
        title_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Cài đặt Hệ thống & Tùy chỉnh Video", self)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        main_layout.addLayout(title_layout)

        # Grid Content
        grid = QGridLayout()
        grid.setSpacing(16)

        # Card 1: Theme & Appearance
        theme_card = CardWidget(self)
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setContentsMargins(18, 16, 18, 16)
        theme_layout.setSpacing(12)
        theme_layout.addWidget(SubtitleLabel("Chủ đề Giao diện (Appearance)", theme_card))

        theme_desc = BodyLabel(
            "Tùy chọn hiển thị chế độ màu sắc Sáng (Light Violet) hoặc Tối (Midnight Dark) theo sở thích.",
            theme_card,
        )
        theme_desc.setWordWrap(True)
        theme_layout.addWidget(theme_desc)

        t_form = QFormLayout()
        t_form.setSpacing(10)

        self.theme_combo = ComboBox(theme_card)
        self.theme_combo.addItem("☀️ Giao diện Sáng (Light Mode)", userData="light")
        self.theme_combo.addItem("🌙 Giao diện Tối (Dark Mode)", userData="dark")

        from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode
        current_m = get_current_theme_mode()
        self.theme_combo.setCurrentIndex(1 if current_m == "dark" else 0)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        t_form.addRow(BodyLabel("Chế độ màu sắc:", theme_card), self.theme_combo)
        theme_layout.addLayout(t_form)
        theme_layout.addStretch(1)
        grid.addWidget(theme_card, 0, 0)

        # Card 2: Video Rendering Pipeline Settings
        video_card = CardWidget(self)
        video_layout = QVBoxLayout(video_card)
        video_layout.setContentsMargins(18, 16, 18, 16)
        video_layout.setSpacing(12)
        video_layout.addWidget(SubtitleLabel("Xử lý & Biên tập Video", video_card))

        v_form = QFormLayout()
        v_form.setSpacing(10)

        self.combo_crop_ratio = ComboBox(video_card)
        self.combo_crop_ratio.addItems(list(PRODUCT_IMAGE_CROP_RATIO_LABELS.values()))
        v_form.addRow(BodyLabel("Tỉ lệ ảnh sản phẩm:", video_card), self.combo_crop_ratio)

        self.combo_motion = ComboBox(video_card)
        self.combo_motion.addItems(list(PRODUCT_IMAGE_MOTION_LABELS.values()))
        v_form.addRow(BodyLabel("Hiệu ứng chuyển động ảnh:", video_card), self.combo_motion)

        self.spin_parallel_items = SpinBox(video_card)
        self.spin_parallel_items.setRange(1, 8)
        self.spin_parallel_items.setValue(getattr(self.config, "max_parallel_session_items", 2) or 2)
        v_form.addRow(BodyLabel("Số luồng render song song:", video_card), self.spin_parallel_items)

        video_layout.addLayout(v_form)
        video_layout.addStretch(1)
        grid.addWidget(video_card, 0, 1)

        # Card 3: Storage Cleaner & Database
        storage_card = CardWidget(self)
        storage_layout = QVBoxLayout(storage_card)
        storage_layout.setContentsMargins(18, 16, 18, 16)
        storage_layout.setSpacing(12)
        storage_layout.addWidget(SubtitleLabel("Dọn dẹp Bộ nhớ & Dữ liệu Tạm", storage_card))

        storage_desc = BodyLabel(
            "Tự động quét và dọn dẹp các tệp video đã render xong, ảnh tạm, thumbnail, và cache không còn sử dụng để giải phóng dung lượng ổ cứng.",
            storage_card,
        )
        storage_desc.setStyleSheet("color: #5F6475;")
        storage_desc.setWordWrap(True)
        storage_layout.addWidget(storage_desc)

        btn_row = QHBoxLayout()
        self.scan_storage_btn = PrimaryPushButton("Quét & Dọn dẹp Storage", storage_card, FIF.DELETE)
        self.scan_storage_btn.clicked.connect(self._on_scan_and_cleanup_storage)
        btn_row.addWidget(self.scan_storage_btn)
        storage_layout.addLayout(btn_row)

        storage_layout.addStretch(1)
        grid.addWidget(storage_card, 1, 0)

        # Card 4: App Information & System Info
        info_card = CardWidget(self)
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(10)
        info_layout.addWidget(SubtitleLabel("Thông tin Hệ thống & Ứng dụng", info_card))

        i_form = QFormLayout()
        i_form.setSpacing(8)

        i_form.addRow(BodyLabel("Tên ứng dụng:", info_card), BodyLabel("TikTok Profile Manager Pro (Qt6 Edition)", info_card))
        i_form.addRow(BodyLabel("Phiên bản:", info_card), BodyLabel("v4.2.0 (PySide6 Fluent UI)", info_card))
        i_form.addRow(BodyLabel("Python Runtime:", info_card), BodyLabel(f"Python {sys.version.split()[0]} ({sys.platform})", info_card))
        i_form.addRow(BodyLabel("Giao diện đồ họa:", info_card), BodyLabel("PySide6 (Qt 6.6+) & Fluent Widgets", info_card))

        info_layout.addLayout(i_form)
        info_layout.addStretch(1)
        grid.addWidget(info_card, 1, 1)

        # Card 5: Gemini API key for Fashion image analysis
        gemini_card = CardWidget(self)
        gemini_layout = QVBoxLayout(gemini_card)
        gemini_layout.setContentsMargins(18, 16, 18, 16)
        gemini_layout.setSpacing(10)
        gemini_layout.addWidget(SubtitleLabel("Gemini API · Fashion", gemini_card))
        gemini_desc = BodyLabel(
            "API key và model này dùng để tạo caption cùng 5 hashtag từ ảnh sản phẩm Fashion. Key chỉ được lưu trong cài đặt cục bộ của ứng dụng.",
            gemini_card,
        )
        gemini_desc.setWordWrap(True)
        gemini_layout.addWidget(gemini_desc)
        self.gemini_api_key_edit = PasswordLineEdit(gemini_card)
        self.gemini_api_key_edit.setEchoMode(PasswordLineEdit.EchoMode.Password)
        self.gemini_api_key_edit.setViewPasswordButtonVisible(False)
        self.gemini_api_key_edit.setPlaceholderText("Dán Gemini API key vào đây")
        self.gemini_api_key_edit.setText(get_gemini_api_key())
        gemini_layout.addWidget(self.gemini_api_key_edit)
        self.gemini_model_combo = ComboBox(gemini_card)
        selected_model = get_gemini_model(DEFAULT_GEMINI_MODEL)
        selected_index = 0
        for index, (model_id, label) in enumerate(GEMINI_MODELS):
            self.gemini_model_combo.addItem(label, userData=model_id)
            if model_id == selected_model:
                selected_index = index
        self.gemini_model_combo.setCurrentIndex(selected_index)
        gemini_layout.addWidget(BodyLabel("Model Gemini:", gemini_card))
        gemini_layout.addWidget(self.gemini_model_combo)
        gemini_actions = QHBoxLayout()
        self.save_gemini_key_btn = PrimaryPushButton("Lưu API key", gemini_card, FIF.SAVE)
        self.save_gemini_key_btn.clicked.connect(self._on_save_gemini_api_key)
        gemini_actions.addWidget(self.save_gemini_key_btn)
        self.toggle_gemini_key_btn = PushButton("Hiện key", gemini_card)
        self.toggle_gemini_key_btn.clicked.connect(self._toggle_gemini_api_key_visibility)
        gemini_actions.addWidget(self.toggle_gemini_key_btn)
        gemini_actions.addStretch(1)
        gemini_layout.addLayout(gemini_actions)
        grid.addWidget(gemini_card, 2, 0, 1, 2)

        main_layout.addLayout(grid, 1)

    def _on_theme_changed(self, index: int) -> None:
        selected_mode = self.theme_combo.itemData(index) or "light"
        win = self.window()
        if hasattr(win, "apply_theme_mode"):
            win.apply_theme_mode(selected_mode)

    def sync_theme_selection(self, mode: str) -> None:
        clean = "dark" if str(mode).strip().lower() == "dark" else "light"
        idx = 1 if clean == "dark" else 0
        if self.theme_combo.currentIndex() != idx:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(idx)
            self.theme_combo.blockSignals(False)

    def _on_save_gemini_api_key(self) -> None:
        save_gemini_api_key(self.gemini_api_key_edit.text())
        save_gemini_model(str(self.gemini_model_combo.currentData() or DEFAULT_GEMINI_MODEL))
        InfoBar.success(
            title="Đã lưu Gemini API key",
            content="Đã lưu API key và model dùng cho bot Fashion.",
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self.window(),
        )

    def _toggle_gemini_api_key_visibility(self) -> None:
        is_hidden = self.gemini_api_key_edit.echoMode() == PasswordLineEdit.EchoMode.Password
        self.gemini_api_key_edit.setEchoMode(
            PasswordLineEdit.EchoMode.Normal if is_hidden else PasswordLineEdit.EchoMode.Password
        )
        self.toggle_gemini_key_btn.setText("Ẩn key" if is_hidden else "Hiện key")

    def _on_scan_and_cleanup_storage(self) -> None:
        try:
            win = self.window()
            manager = getattr(win, "manager", None)
            phone_view = getattr(win, "phone_view", None)
            phone_ctrl = getattr(phone_view, "phone_controller", None) if phone_view else None
            project_root = getattr(manager, "project_root", ".")

            dialog = CleanupDialog(
                parent=win,
                config=self.config,
                project_root=project_root,
                phone_controller=phone_ctrl,
                manager=manager,
            )
            if dialog.exec() and dialog.confirmed:
                selected_keys = dialog.get_selected_keys()
                if not selected_keys:
                    return
                report = execute_granular_cleanup(
                    selected_keys=selected_keys,
                    config=self.config,
                    project_root=project_root,
                    manager=manager,
                    phone_controller=phone_ctrl,
                )
                msg = format_granular_cleanup_report(report)
                if manager and hasattr(manager, "add_log"):
                    manager.add_log("info", "system_cleanup", msg)
                if hasattr(win, "dashboard_view") and hasattr(win.dashboard_view, "refresh_dashboard"):
                    win.dashboard_view.refresh_dashboard()
                InfoBar.success(
                    title="Đã dọn dẹp hệ thống",
                    content=msg,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=win,
                )
        except Exception as exc:
            InfoBar.error("Lỗi dọn dẹp", str(exc), parent=self.window())
