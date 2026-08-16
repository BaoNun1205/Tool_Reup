from __future__ import annotations

from dataclasses import replace
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PlainTextEdit,
    ProgressBar,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)

from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec
from auto_tiktok_editor.tiktok_profiles.models import PUBLISH_MODES
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.tag_bar import YouTubeTagInput
from auto_tiktok_editor.tiktok_profiles.qt_ui.dialogs.schedule_dialog import ScheduleDialog
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import format_video_status, format_vietnam_datetime
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import WorkerThread
from auto_tiktok_editor.tiktok_profiles.telegram_queue import copy_rendered_video_to_queue


class VideosView(QWidget):
    """View to manage videos, edit captions/hashtags, and manage queue."""

    def __init__(
        self,
        manager: TikTokProfileManager,
        config: PipelineConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.config = config or PipelineConfig.from_env()
        self._current_account_name: str | None = None
        self._videos_cache = []
        self._selected_video = None
        self._select_mode = False
        self._updating_inspector = False
        self._active_workers: list[WorkerThread] = []

        self._init_ui()
        self.refresh_profiles_list()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title
        self.title_label = SubtitleLabel("Quản lý Video & Hàng đợi Đăng bài", self)
        main_layout.addWidget(self.title_label)

        # Action Toolbar (Unified row with action buttons and profile filter)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.add_video_btn = PrimaryPushButton("Thêm Video", self, FIF.ADD)
        self.add_video_btn.clicked.connect(self._on_add_video)
        toolbar.addWidget(self.add_video_btn)

        toolbar.addWidget(BodyLabel("Chọn Profile:", self))
        self.profile_combo = ComboBox(self)
        self.profile_combo.setFixedWidth(190)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        toolbar.addWidget(self.profile_combo)

        self.refresh_btn = PushButton("Làm mới", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_videos)
        toolbar.addWidget(self.refresh_btn)

        self.select_mode_btn = PushButton("Chọn", self, FIF.CHECKBOX)
        self.select_mode_btn.clicked.connect(self._toggle_select_mode)
        toolbar.addWidget(self.select_mode_btn)

        self.schedule_btn = PushButton("Đặt lịch đăng", self, FIF.DATE_TIME)
        self.schedule_btn.clicked.connect(self._on_schedule_video)
        toolbar.addWidget(self.schedule_btn)

        toolbar.addStretch(1)

        main_layout.addLayout(toolbar)

        # Splitter: Table (Left) + Inspector/Editor (Right)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # Left: Table
        table_container = QWidget(splitter)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 8, 0)

        self.table = TableWidget(table_container)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "☐",
            "Product ID",
            "Cut Mode",
            "Caption",
            "Hashtag",
            "Trạng thái",
            "Hành động",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_video_selection_changed)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)

        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 140)

        # Initially hidden selection column
        self.table.setColumnHidden(0, True)

        table_layout.addWidget(self.table)
        splitter.addWidget(table_container)

        # Right: Inspector Card
        inspector_card = CardWidget(splitter)
        inspector_card.setMinimumWidth(280)
        inspector_layout = QVBoxLayout(inspector_card)
        inspector_layout.setContentsMargins(18, 16, 18, 16)
        inspector_layout.setSpacing(12)

        inspector_layout.addWidget(SubtitleLabel("Chi tiết & Chỉnh sửa nhanh", inspector_card))

        self.edit_filename_label = BodyLabel("Chưa chọn video nào", inspector_card)
        self.edit_filename_label.setStyleSheet("color: #5F6475; font-weight: bold;")
        inspector_layout.addWidget(self.edit_filename_label)

        inspector_layout.addWidget(BodyLabel("Profile sở hữu:", inspector_card))
        self.edit_profile_combo = ComboBox(inspector_card)
        inspector_layout.addWidget(self.edit_profile_combo)

        inspector_layout.addWidget(BodyLabel("Caption:", inspector_card))
        self.edit_caption = LineEdit(inspector_card)
        self.edit_caption.setPlaceholderText("Mô tả video...")
        inspector_layout.addWidget(self.edit_caption)

        inspector_layout.addWidget(BodyLabel("Hashtags:", inspector_card))
        self.tag_input = YouTubeTagInput("", inspector_card)
        inspector_layout.addWidget(self.tag_input)

        inspector_layout.addWidget(BodyLabel("Product ID:", inspector_card))
        self.edit_product_id = LineEdit(inspector_card)
        self.edit_product_id.setPlaceholderText("Mã sản phẩm Affiliate...")
        inspector_layout.addWidget(self.edit_product_id)

        inspector_layout.addWidget(BodyLabel("Thời gian hẹn đăng:", inspector_card))
        self.edit_scheduled_at = LineEdit(inspector_card)
        self.edit_scheduled_at.setPlaceholderText("VD: 2026-08-20 18:30 (để trống nếu đăng ngay)")
        inspector_layout.addWidget(self.edit_scheduled_at)

        inspector_layout.addWidget(BodyLabel("Cut Mode:", inspector_card))
        self.edit_cut_mode = ComboBox(inspector_card)
        self.edit_cut_mode.addItem("Cắt cố định", userData="fixed")
        self.edit_cut_mode.addItem("Cắt theo đổi cảnh", userData="scene")
        self.edit_cut_mode.addItem("Giữ nguyên video gốc", userData="original")
        inspector_layout.addWidget(self.edit_cut_mode)

        self.tag_input.tags_changed.connect(self._on_tags_auto_saved)
        self.edit_caption.textChanged.connect(self._on_caption_auto_saved)
        self.edit_product_id.textChanged.connect(self._on_product_id_auto_saved)
        self.edit_scheduled_at.textChanged.connect(self._on_scheduled_at_auto_saved)
        self.edit_profile_combo.currentIndexChanged.connect(self._on_profile_auto_saved)
        self.edit_cut_mode.currentIndexChanged.connect(self._on_inspector_cut_mode_changed)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_detail_btn = PrimaryPushButton("Lưu thay đổi", inspector_card, FIF.SAVE)
        self.save_detail_btn.clicked.connect(self._on_save_detail)
        save_row.addWidget(self.save_detail_btn)
        inspector_layout.addLayout(save_row)

        inspector_layout.addStretch(1)
        splitter.addWidget(inspector_card)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([650, 350])
        main_layout.addWidget(splitter, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_profiles_list()

    def refresh_profiles_list(self) -> None:
        """Populate profile combobox."""
        try:
            accounts = self.manager.list_accounts()
            self.profile_combo.blockSignals(True)
            self.profile_combo.clear()
            self.profile_combo.addItem("Tất cả Profile")
            for a in accounts:
                self.profile_combo.addItem(a.name)
            self.profile_combo.blockSignals(False)

            self.edit_profile_combo.blockSignals(True)
            self.edit_profile_combo.clear()
            for a in accounts:
                self.edit_profile_combo.addItem(a.name, userData=a.id)
            self.edit_profile_combo.blockSignals(False)

            if self._current_account_name:
                self.set_active_profile(self._current_account_name)
            else:
                self.refresh_videos()
        except Exception as exc:
            pass

    def set_active_profile(self, account_name: str) -> None:
        self._current_account_name = account_name
        idx = self.profile_combo.findText(account_name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.refresh_videos()

    def _on_profile_changed(self) -> None:
        text = self.profile_combo.currentText()
        self._current_account_name = None if text == "Tất cả Profile" else text
        self.refresh_videos()

    def apply_theme_mode(self, mode: str) -> None:
        """Update tag input, labels, and status cells for active theme."""
        clean = "dark" if str(mode).strip().lower() == "dark" else "light"
        self.tag_input.set_theme_mode(clean)
        self.edit_filename_label.setStyleSheet("color: #B5B9C7; font-weight: bold;" if clean == "dark" else "color: #5F6475; font-weight: bold;")
        self.refresh_videos()

    def refresh_videos(self) -> None:
        """Load videos from database and update table."""
        try:
            all_videos = self.manager.list_videos()
            if self._current_account_name and self._current_account_name != "Tất cả Profile":
                accounts = {a.name: a.id for a in self.manager.list_accounts()}
                target_id = accounts.get(self._current_account_name)
                self._videos_cache = [v for v in all_videos if v.account_id == target_id]
            else:
                self._videos_cache = all_videos

            self._populate_table(self._videos_cache)
        except Exception as exc:
            InfoBar.error(
                title="Lỗi tải danh sách video",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _populate_table(self, videos: list[Any]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(videos))
        for row, v in enumerate(videos):
            # Col 0: Checkbox for selection
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Unchecked)
            chk_item.setData(Qt.ItemDataRole.UserRole, v)

            # Col 1: Product ID
            product_val = getattr(v, "product_id", "") or ""
            product_item = QTableWidgetItem(product_val)
            product_item.setData(Qt.ItemDataRole.UserRole, v)

            # Col 2: Cut Mode ComboBox
            cut_mode_combo = ComboBox(self.table)
            cut_mode_combo.setFixedHeight(28)
            cut_mode_combo.addItem("Cắt cố định", userData="fixed")
            cut_mode_combo.addItem("Cắt theo đổi cảnh", userData="scene")
            cut_mode_combo.addItem("Giữ nguyên video gốc", userData="original")
            current_mode = str(getattr(v, "cut_mode", "") or "fixed").strip().lower()
            if current_mode in ("scene", "smart"):
                idx = 1
            elif current_mode == "original":
                idx = 2
            else:
                idx = 0
            cut_mode_combo.setCurrentIndex(idx)
            cut_mode_combo.currentIndexChanged.connect(
                lambda index, vid=v, cb=cut_mode_combo: self._on_cut_mode_changed(vid, cb.itemData(index))
            )

            # Col 3: Caption
            caption_val = getattr(v, "caption", "") or ""
            caption_item = QTableWidgetItem(caption_val)

            # Col 4: Hashtag
            hashtags_val = getattr(v, "hashtags", "") or ""
            hashtags_item = QTableWidgetItem(hashtags_val)

            # Col 5: Trạng thái (Tiếng Việt & Chấm LED phát sáng)
            status_val = getattr(v, "status", "pending") or "pending"
            status_text, status_color = format_video_status(status_val)
            status_item = QTableWidgetItem(status_text)
            status_font = status_item.font()
            status_font.setBold(True)
            status_item.setFont(status_font)
            status_item.setForeground(QColor(status_color))

            self.table.setItem(row, 0, chk_item)
            self.table.setItem(row, 1, product_item)
            self.table.setCellWidget(row, 2, cut_mode_combo)
            self.table.setItem(row, 3, caption_item)
            self.table.setItem(row, 4, hashtags_item)
            self.table.setItem(row, 5, status_item)

            # Col 6: Hành động (Tạo lại, Gửi, Xem, Xóa)
            action_widget = QWidget(self.table)
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            status_lower = str(status_val).strip().lower()
            is_ready = status_lower in ("ready", "published", "prepared")
            is_rendering = status_lower == "rendering"

            # 1. Nút Tạo lại (Icon)
            btn_render = ToolButton(action_widget)
            btn_render.setIcon(FIF.SYNC)
            btn_render.setFixedHeight(28)
            btn_render.setEnabled(not is_rendering)
            btn_render.setToolTip("Đang tạo video..." if is_rendering else ("Tạo lại video (Re-render)" if status_lower != "draft" else "Tạo video"))
            btn_render.clicked.connect(lambda _, vid=v: self._on_re_render_video(vid))
            action_layout.addWidget(btn_render)

            # 2. Nút Gửi (Gửi sang điện thoại - Disabled khi chưa tạo xong)
            btn_send = ToolButton(action_widget)
            btn_send.setIcon(FIF.SEND)
            btn_send.setFixedHeight(28)
            btn_send.setEnabled(is_ready)
            btn_send.setToolTip("Gửi video sang điện thoại & copy clipboard" if is_ready else "Video chưa tạo xong, không thể gửi")
            btn_send.clicked.connect(lambda _, vid=v: self._on_send_video(vid))
            action_layout.addWidget(btn_send)

            # 3. Nút Xem (Play - Disabled khi chưa tạo xong)
            btn_play = ToolButton(action_widget)
            btn_play.setIcon(FIF.PLAY)
            btn_play.setFixedHeight(28)
            btn_play.setEnabled(is_ready)
            btn_play.setToolTip("Xem trước video" if is_ready else "Video chưa tạo xong, không thể xem")
            btn_play.clicked.connect(lambda _, vid=v: self._on_play_video(vid))
            action_layout.addWidget(btn_play)

            # 4. Nút Xóa
            btn_del = ToolButton(action_widget)
            btn_del.setIcon(FIF.DELETE)
            btn_del.setFixedHeight(28)
            btn_del.setToolTip("Xóa video này")
            btn_del.clicked.connect(lambda _, vid=v: self._on_delete_video(vid))
            action_layout.addWidget(btn_del)

            self.table.setCellWidget(row, 6, action_widget)

        self.table.blockSignals(False)
        self.table.setColumnHidden(0, not self._select_mode)
        self._update_header_checkbox()
        self._update_selection_ui()

    def get_selected_videos(self) -> list[Any]:
        """Return all videos that are either checked via checkbox or row-selected."""
        videos = []
        seen_ids = set()

        # 1. Check checkboxes in column 0 if select mode active
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                v = item.data(Qt.ItemDataRole.UserRole)
                if v and v.id not in seen_ids:
                    videos.append(v)
                    seen_ids.add(v.id)

        # 2. Check highlighted rows
        if not videos:
            for index in self.table.selectionModel().selectedRows():
                item = self.table.item(index.row(), 0) or self.table.item(index.row(), 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id not in seen_ids:
                        videos.append(v)
                        seen_ids.add(v.id)

        # 3. Fallback to cached selected video
        if not videos and self._selected_video:
            videos.append(self._selected_video)

        return videos

    def get_selected_video(self) -> Any | None:
        videos = self.get_selected_videos()
        return videos[0] if videos else None

    def _toggle_select_mode(self) -> None:
        self._select_mode = not self._select_mode
        self.table.setColumnHidden(0, not self._select_mode)
        self.select_mode_btn.setText("Hủy chọn" if self._select_mode else "Chọn")
        if not self._select_mode:
            # Uncheck all when exiting select mode
            self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.table.blockSignals(False)
        self._update_header_checkbox()
        self._update_selection_ui()

    def _on_header_section_clicked(self, logical_index: int) -> None:
        if logical_index == 0 and self._select_mode:
            all_checked = all(
                self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                for row in range(self.table.rowCount())
                if self.table.item(row, 0)
            ) if self.table.rowCount() > 0 else False
            new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
            self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(new_state)
            self.table.blockSignals(False)
            self._update_header_checkbox()
            self._update_selection_ui()

    def _update_header_checkbox(self) -> None:
        if self.table.rowCount() == 0:
            header_text = "☐"
        else:
            all_checked = all(
                self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                for row in range(self.table.rowCount())
                if self.table.item(row, 0)
            )
            header_text = "☑" if all_checked else "☐"
        header_item = QTableWidgetItem(header_text)
        self.table.setHorizontalHeaderItem(0, header_item)

    def _on_cut_mode_changed(self, video: Any, new_mode: str) -> None:
        try:
            updated = self.manager.update_video_cut_mode(video.id, new_mode)
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0) or self.table.item(r, 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id == video.id:
                        item.setData(Qt.ItemDataRole.UserRole, updated)
            if self._selected_video and self._selected_video.id == video.id:
                self._selected_video = updated
                if new_mode in ("scene", "smart"):
                    idx = 1
                elif new_mode == "original":
                    idx = 2
                else:
                    idx = 0
                self.edit_cut_mode.blockSignals(True)
                self.edit_cut_mode.setCurrentIndex(idx)
                self.edit_cut_mode.blockSignals(False)
        except Exception as exc:
            InfoBar.error("Lỗi đổi Cut Mode", str(exc), parent=self.window())

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._update_header_checkbox()
            self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        selected = self.get_selected_videos()
        count = len(selected)
        if hasattr(self, "schedule_btn"):
            if count > 0 and self._select_mode:
                self.schedule_btn.setText(f"Đặt lịch đăng ({count})")
            else:
                self.schedule_btn.setText("Đặt lịch đăng")

    def _on_video_selection_changed(self) -> None:
        self._updating_inspector = True
        try:
            video = self.get_selected_video()
            self._selected_video = video
            if video:
                fname = Path(getattr(video, "file_path", "") or "").name or f"Video #{video.id}"
                self.edit_filename_label.setText(fname)
                self.edit_caption.setText(getattr(video, "caption", "") or "")
                self.tag_input.set_tags(getattr(video, "hashtags", "") or "")
                self.edit_product_id.setText(getattr(video, "product_id", "") or "")
                self.edit_scheduled_at.setText(getattr(video, "scheduled_at", "") or "")

                target_account_id = getattr(video, "account_id", None)
                idx_prof = -1
                for i in range(self.edit_profile_combo.count()):
                    if self.edit_profile_combo.itemData(i) == target_account_id:
                        idx_prof = i
                        break
                self.edit_profile_combo.blockSignals(True)
                if idx_prof >= 0:
                    self.edit_profile_combo.setCurrentIndex(idx_prof)
                elif self.edit_profile_combo.count() > 0:
                    self.edit_profile_combo.setCurrentIndex(0)
                self.edit_profile_combo.blockSignals(False)

                current_mode = str(getattr(video, "cut_mode", "") or "fixed").strip().lower()
                if current_mode in ("scene", "smart"):
                    idx = 1
                elif current_mode == "original":
                    idx = 2
                else:
                    idx = 0
                self.edit_cut_mode.blockSignals(True)
                self.edit_cut_mode.setCurrentIndex(idx)
                self.edit_cut_mode.blockSignals(False)
            else:
                self.edit_filename_label.setText("Chưa chọn video nào")
                self.edit_caption.clear()
                self.tag_input.set_tags("")
                self.edit_product_id.clear()
                self.edit_scheduled_at.clear()
                self.edit_cut_mode.setCurrentIndex(0)
        finally:
            self._updating_inspector = False
        self._update_selection_ui()

    def _on_tags_auto_saved(self, tags_str: str) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        try:
            scheduled_str = self.edit_scheduled_at.text().strip()
            publish_mode = "scheduled" if scheduled_str else "now"
            updated = self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=self.edit_caption.text().strip(),
                hashtags=tags_str,
                product_id=self.edit_product_id.text().strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=scheduled_str,
                account_id=self._selected_video.account_id,
            )
            self._selected_video = updated
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0) or self.table.item(row, 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id == updated.id:
                        h_item = self.table.item(row, 4)
                        if h_item:
                            h_item.setText(tags_str)
                        break
        except Exception:
            pass

    def _on_caption_auto_saved(self, caption_str: str) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        try:
            scheduled_str = self.edit_scheduled_at.text().strip()
            publish_mode = "scheduled" if scheduled_str else "now"
            updated = self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=caption_str.strip(),
                hashtags=self.tag_input.get_tags_string(),
                product_id=self.edit_product_id.text().strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=scheduled_str,
                account_id=self._selected_video.account_id,
            )
            self._selected_video = updated
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0) or self.table.item(row, 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id == updated.id:
                        c_item = self.table.item(row, 3)
                        if c_item:
                            c_item.setText(caption_str)
                        break
        except Exception:
            pass

    def _on_product_id_auto_saved(self, pid_str: str) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        try:
            scheduled_str = self.edit_scheduled_at.text().strip()
            publish_mode = "scheduled" if scheduled_str else "now"
            updated = self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=self.edit_caption.text().strip(),
                hashtags=self.tag_input.get_tags_string(),
                product_id=pid_str.strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=scheduled_str,
                account_id=self._selected_video.account_id,
            )
            self._selected_video = updated
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0) or self.table.item(row, 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id == updated.id:
                        p_item = self.table.item(row, 1)
                        if p_item:
                            p_item.setText(pid_str)
                        break
        except Exception:
            pass

    def _on_scheduled_at_auto_saved(self, sched_str: str) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        try:
            publish_mode = "scheduled" if sched_str.strip() else "now"
            new_account_id = self.edit_profile_combo.currentData()
            updated = self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=self.edit_caption.text().strip(),
                hashtags=self.tag_input.get_tags_string(),
                product_id=self.edit_product_id.text().strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=sched_str.strip(),
                account_id=new_account_id if new_account_id is not None else self._selected_video.account_id,
            )
            self._selected_video = updated
        except Exception:
            pass

    def _on_profile_auto_saved(self, index: int) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        new_account_id = self.edit_profile_combo.itemData(index)
        if new_account_id == getattr(self._selected_video, "account_id", None):
            return
        try:
            scheduled_str = self.edit_scheduled_at.text().strip()
            publish_mode = "scheduled" if scheduled_str else "now"
            updated = self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=self.edit_caption.text().strip(),
                hashtags=self.tag_input.get_tags_string(),
                product_id=self.edit_product_id.text().strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=scheduled_str,
                account_id=new_account_id,
            )
            self._selected_video = updated
            if self._current_account_name and self._current_account_name != "Tất cả Profile":
                self.refresh_videos()
        except Exception as exc:
            InfoBar.error("Lỗi đổi Profile", str(exc), parent=self.window())

    def _on_inspector_cut_mode_changed(self, index: int) -> None:
        if not self._selected_video or self._updating_inspector:
            return
        new_mode = self.edit_cut_mode.itemData(index) or "fixed"
        if new_mode == getattr(self._selected_video, "cut_mode", None):
            return
        try:
            updated = self.manager.update_video_cut_mode(self._selected_video.id, new_mode)
            self._selected_video = updated
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0) or self.table.item(r, 1)
                if item:
                    v = item.data(Qt.ItemDataRole.UserRole)
                    if v and v.id == updated.id:
                        item.setData(Qt.ItemDataRole.UserRole, updated)
                        row_combo = self.table.cellWidget(r, 2)
                        if isinstance(row_combo, ComboBox):
                            row_combo.blockSignals(True)
                            if new_mode in ("scene", "smart"):
                                row_combo.setCurrentIndex(1)
                            elif new_mode == "original":
                                row_combo.setCurrentIndex(2)
                            else:
                                row_combo.setCurrentIndex(0)
                            row_combo.blockSignals(False)
                        break
        except Exception as exc:
            InfoBar.error("Lỗi đổi Cut Mode", str(exc), parent=self.window())

    def _on_save_detail(self) -> None:
        if not self._selected_video:
            InfoBar.warning(
                title="Chưa chọn video",
                content="Vui lòng chọn 1 video từ bảng trước khi lưu!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        try:
            scheduled_str = self.edit_scheduled_at.text().strip()
            publish_mode = "scheduled" if scheduled_str else "now"
            cut_mode = self.edit_cut_mode.currentData() or "fixed"
            new_account_id = self.edit_profile_combo.currentData()
            self.manager.update_video_details(
                video_id=self._selected_video.id,
                caption=self.edit_caption.text().strip(),
                hashtags=self.tag_input.get_tags_string(),
                product_id=self.edit_product_id.text().strip(),
                note=getattr(self._selected_video, "note", "") or "",
                publish_mode=publish_mode,
                scheduled_at=scheduled_str,
                account_id=new_account_id if new_account_id is not None else self._selected_video.account_id,
            )
            updated_cut = self.manager.update_video_cut_mode(self._selected_video.id, cut_mode)
            self._selected_video = updated_cut
            InfoBar.success(
                title="Đã lưu",
                content="Cập nhật thông tin video thành công!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            self.refresh_videos()
        except Exception as exc:
            InfoBar.error(
                title="Lỗi lưu video",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _on_re_render_video(self, video: Any) -> None:
        if not video:
            return

        def _worker_fn() -> dict:
            fresh_video = self.manager.get_video(video.id) or video
            self.manager.update_video_status(fresh_video.id, "rendering")

            account = self.manager.get_account(fresh_video.account_id) if fresh_video.account_id else None
            account_cut_mode = str(getattr(account, "cut_mode", "") or "").strip().lower()
            cut_mode = str(getattr(fresh_video, "cut_mode", "") or account_cut_mode or "fixed").strip().lower()
            render_config = replace(self.config, video_cut_mode=cut_mode)

            source_url = str(getattr(fresh_video, "source_video_url", "") or "").strip()
            raw_product_image = str(getattr(fresh_video, "product_image_path", "") or "").strip()
            resolved_product_image = Path(raw_product_image)
            if not resolved_product_image.is_absolute():
                resolved_product_image = (Path(self.manager.project_root) / resolved_product_image).resolve()

            if source_url and resolved_product_image.exists() and resolved_product_image.is_file():
                orchestrator = SessionOrchestrator(config=render_config)
                session_result = orchestrator.run(
                    SessionSpec(
                        items=[
                            SessionItemSpec(
                                row_id=f"profile_video_{fresh_video.id}",
                                source_video_url=source_url,
                                product_image=resolved_product_image,
                            )
                        ],
                        output_root_dir=render_config.default_output_root,
                        session_name=f"profile_video_{fresh_video.id}",
                    )
                )
                if session_result.items and session_result.items[0].status == "completed":
                    final_path = session_result.items[0].artifacts.final_video_path
                    profile_slug = account.name if account else "default"
                    stored_path = copy_rendered_video_to_queue(profile_slug, final_path)
                    updated = self.manager.mark_video_rendered(
                        fresh_video.id,
                        stored_path,
                        source_title=str(session_result.items[0].metadata.get("source_title") or "").strip() or None,
                    )
                    self.manager.add_log(
                        "info",
                        "video_render_completed",
                        "Rendered video %s with cut mode %s." % (fresh_video.id, cut_mode),
                        account_id=updated.account_id,
                        video_id=fresh_video.id,
                    )
                    return {"video_id": fresh_video.id, "path": stored_path}
                err_msg = session_result.items[0].error if session_result.items else "Quá trình render không tạo ra video hoàn chỉnh."
                raise RuntimeError(err_msg)

            # Fallback: if already has existing file_path, mark ready
            file_path = self.manager.resolve_video_path(fresh_video)
            if file_path.exists():
                self.manager.update_video_status(fresh_video.id, "ready")
                return {"video_id": fresh_video.id, "path": file_path}

            raise ValueError(f"Video #{fresh_video.id} không có URL nguồn hoặc file ảnh sản phẩm để render lại.")

        def _on_done(result: Any) -> None:
            self.refresh_videos()
            InfoBar.success(
                title="Đã tạo xong video",
                content=f"Tạo thành công video ID {video.id}!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

        def _on_err(exc: Exception, tb: str) -> None:
            self.manager.update_video_status(video.id, "error", note=str(exc))
            self.refresh_videos()
            InfoBar.error(
                title="Lỗi tạo video",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

        InfoBar.info(
            title="Đang tạo video",
            content=f"Bắt đầu render lại video ID {video.id}...",
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )
        self.manager.update_video_status(video.id, "queued")
        self.refresh_videos()

        thread = WorkerThread(_worker_fn, parent=self)
        self._active_workers.append(thread)

        def _cleanup():
            if thread in self._active_workers:
                self._active_workers.remove(thread)

        thread.finished_task.connect(_on_done)
        thread.finished_task.connect(_cleanup)
        thread.error_task.connect(_on_err)
        thread.error_task.connect(_cleanup)
        thread.start()

    def _on_send_video(self, single_video: Any | None = None) -> None:
        targets = [single_video] if single_video else self.get_selected_videos()
        if not targets:
            InfoBar.warning(
                title="Chưa chọn video",
                content="Vui lòng chọn video để gửi sang điện thoại!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        from auto_tiktok_editor.phone_control import PhoneController, load_phone_control_settings
        phone_settings = load_phone_control_settings()
        address = str(getattr(phone_settings, "address", "") or "").strip()
        controller = PhoneController(self.config)
        controller.runner.ensure_tool(self.config.adb_bin)
        completed = controller.runner.run([self.config.adb_bin, "devices"], check=False)
        device_serials = [
            line.split("\t", 1)[0].strip()
            for line in completed.stdout.splitlines()
            if line.strip() and not line.startswith("List of") and "\tdevice" in line
        ]

        target_address = address
        if not target_address:
            if len(device_serials) == 1:
                target_address = device_serials[0]
            elif not device_serials:
                InfoBar.warning(
                    title="Chưa kết nối ADB",
                    content="Vui lòng vào tab 'Điện thoại (ADB)' và kết nối điện thoại trước khi gửi!",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
                return
            else:
                target_address = device_serials[0]

        video = targets[0]
        status_lower = str(getattr(video, "status", "") or "").strip().lower()
        if status_lower not in ("ready", "published", "prepared"):
            InfoBar.warning(
                title="Chưa thể gửi",
                content=f"Video #{video.id} chưa tạo xong, vui lòng chờ hoàn thành trước khi gửi!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        def _send_worker() -> dict:
            video_path = self.manager.resolve_video_path(video)
            if not video_path.exists():
                raise FileNotFoundError(f"Không tìm thấy file video: {video_path}")

            fresh_video = self.manager.get_video(video.id) or video
            if self._selected_video and self._selected_video.id == video.id:
                caption_val = self.edit_caption.text().strip()
                hashtags_val = self.tag_input.get_tags_string().strip()
                product_id_val = self.edit_product_id.text().strip()
            else:
                caption_val = (getattr(fresh_video, "caption", "") or "").strip()
                hashtags_val = (getattr(fresh_video, "hashtags", "") or "").strip()
                product_id_val = (getattr(fresh_video, "product_id", "") or "").strip()

            caption_text = f"{caption_val} {hashtags_val}".strip() if hashtags_val else caption_val
            product_id = product_id_val

            # 1. Gửi video vào thư viện (Gallery) điện thoại
            phone_result = controller.send_file_to_gallery(target_address, video_path)

            # 2. Copy caption + hashtag vào bộ nhớ tạm
            if caption_text:
                controller.copy_text_to_clipboard(
                    caption_text,
                    label="Description and hashtags",
                    address=target_address,
                    sync_to_phone=True,
                    require_phone_clipboard=False,
                )

            # 3. Copy Product ID vào bộ nhớ tạm
            if product_id:
                controller.copy_text_to_clipboard(
                    product_id,
                    label="Product ID",
                    address=target_address,
                    sync_to_phone=True,
                    require_phone_clipboard=False,
                )

            self.manager.add_log(
                "info",
                "phone_video_send",
                f"Đã gửi video và copy mô tả/hashtag + Product ID vào clipboard: {phone_result.get('remote_path', '')}",
                account_id=video.account_id,
                video_id=video.id,
            )
            return {"video_id": video.id, "phone_result": phone_result}

        def _on_done(result: Any) -> None:
            self.refresh_videos()
            InfoBar.success(
                title="Gửi thành công",
                content=f"Đã gửi video ID {video.id} và copy mô tả/hashtag + Product ID vào clipboard điện thoại.",
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self.window(),
            )

        def _on_err(exc: Exception, tb: str) -> None:
            InfoBar.error(
                title="Lỗi gửi video",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

        InfoBar.info(
            title="Đang gửi video",
            content=f"Đang đẩy video ID {video.id} sang thiết bị {address}...",
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )
        thread = WorkerThread(_send_worker, parent=self)
        self._active_workers.append(thread)

        def _cleanup():
            if thread in self._active_workers:
                self._active_workers.remove(thread)

        thread.finished_task.connect(_on_done)
        thread.finished_task.connect(_cleanup)
        thread.error_task.connect(_on_err)
        thread.error_task.connect(_cleanup)
        thread.start()

    def _on_play_video(self, video: Any) -> None:
        if not video:
            return
        status_lower = str(getattr(video, "status", "") or "").strip().lower()
        if status_lower not in ("ready", "published", "prepared"):
            InfoBar.warning(
                title="Chưa thể xem",
                content=f"Video #{video.id} chưa tạo xong hoặc đang render.",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return
        try:
            path = self.manager.resolve_video_path(video)
            if path.exists() and path.is_file():
                if sys.platform == "win32":
                    os.startfile(str(path))
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            else:
                InfoBar.warning(
                    title="File không tồn tại",
                    content=f"Không tìm thấy file video: {path}",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
        except Exception as exc:
            InfoBar.error("Lỗi mở video", str(exc), parent=self.window())

    def _on_add_video(self) -> None:
        dialog = VideoDialog(self.window(), is_edit=False)
        if dialog.exec():
            data = dialog.result_data
            if data:
                try:
                    accounts = self.manager.list_accounts()
                    account_id = None
                    if self._current_account_name and self._current_account_name != "Tất cả Profile":
                        acc_map = {a.name: a.id for a in accounts}
                        account_id = acc_map.get(self._current_account_name)
                    elif accounts:
                        account_id = accounts[0].id

                    self.manager.add_video(
                        file_path=data["file_path"],
                        caption=data["caption"],
                        hashtags=data["hashtags"],
                        product_id=data["product_id"],
                        publish_mode=data["publish_mode"],
                        scheduled_at=data["scheduled_at"],
                        note=data["note"],
                        account_id=account_id,
                    )
                    InfoBar.success(
                        title="Thành công",
                        content="Đã thêm video vào hàng đợi.",
                        position=InfoBarPosition.TOP,
                        parent=self.window(),
                    )
                    self.refresh_videos()
                except Exception as exc:
                    InfoBar.error(
                        title="Lỗi thêm video",
                        content=str(exc),
                        position=InfoBarPosition.TOP,
                        parent=self.window(),
                    )

    def _on_schedule_video(self) -> None:
        video = self.get_selected_video()
        if not video:
            InfoBar.warning(
                title="Chưa chọn video",
                content="Vui lòng chọn 1 video để đặt lịch đăng!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        dialog = ScheduleDialog(self.window(), video=video)
        if dialog.exec():
            data = dialog.result_data
            if data:
                try:
                    self.manager.update_video_schedule(
                        video_id=video.id,
                        publish_mode=data["publish_mode"],
                        scheduled_at=data["scheduled_at"],
                        product_id=data["product_id"],
                    )
                    InfoBar.success(
                        title="Đã cập nhật lịch",
                        content="Cập nhật lịch đăng video thành công.",
                        position=InfoBarPosition.TOP,
                        parent=self.window(),
                    )
                    self.refresh_videos()
                except Exception as exc:
                    InfoBar.error(
                        title="Lỗi cập nhật lịch",
                        content=str(exc),
                        position=InfoBarPosition.TOP,
                        parent=self.window(),
                    )

    def _on_delete_video(self, single_video: Any | None = None) -> None:
        videos_to_delete = [single_video] if single_video else self.get_selected_videos()
        if not videos_to_delete:
            InfoBar.warning(
                title="Chưa chọn video",
                content="Vui lòng tích chọn hoặc nhấp chọn các video cần xóa!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        count = len(videos_to_delete)
        title = "Xác nhận xóa video" if count == 1 else f"Xác nhận xóa {count} video"
        msg = (
            f"Bạn có chắc muốn xóa video '{getattr(videos_to_delete[0], 'product_id', '') or videos_to_delete[0].id}' khỏi danh sách?"
            if count == 1
            else f"Bạn có chắc muốn xóa toàn bộ {count} video đã chọn khỏi danh sách?"
        )

        box = MessageBox(
            title=title,
            content=msg,
            parent=self.window(),
        )
        if box.exec():
            try:
                ids = [v.id for v in videos_to_delete]
                with self.manager._connect() as conn:
                    conn.executemany("DELETE FROM videos WHERE id = ?", [(vid,) for vid in ids])
                InfoBar.success(
                    title="Đã xóa",
                    content=f"Đã xóa thành công {count} video khỏi danh sách.",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
                self.refresh_videos()
            except Exception as exc:
                InfoBar.error(
                    title="Lỗi xóa video",
                    content=str(exc),
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )

    def _on_open_video_folder(self) -> None:
        video = self.get_selected_video()
        if not video:
            return
        file_path = getattr(video, "file_path", "")
        if file_path and Path(file_path).exists():
            folder = str(Path(file_path).parent)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])

    def _show_context_menu(self, pos: QPoint) -> None:
        video = self.get_selected_video()
        if not video:
            return

        status_lower = str(getattr(video, "status", "") or "").strip().lower()
        is_ready = status_lower in ("ready", "published", "prepared")
        is_rendering = status_lower == "rendering"

        menu = RoundMenu(parent=self)

        act_render = Action(FIF.SYNC, "↻ Tạo lại video này", self)
        act_render.setEnabled(not is_rendering)
        act_render.triggered.connect(lambda: self._on_re_render_video(video))
        menu.addAction(act_render)

        act_send = Action(FIF.SEND, "Gửi sang điện thoại (Video + Clipboard)", self)
        act_send.setEnabled(is_ready)
        act_send.triggered.connect(lambda: self._on_send_video(video))
        menu.addAction(act_send)

        act_play = Action(FIF.PLAY, "▶ Xem trước video", self)
        act_play.setEnabled(is_ready)
        act_play.triggered.connect(lambda: self._on_play_video(video))
        menu.addAction(act_play)

        menu.addSeparator()

        act_schedule = Action(FIF.DATE_TIME, "Đặt lịch đăng", self)
        act_schedule.triggered.connect(self._on_schedule_video)
        menu.addAction(act_schedule)

        act_folder = Action(FIF.FOLDER, "Mở thư mục chứa video", self)
        act_folder.triggered.connect(self._on_open_video_folder)
        menu.addAction(act_folder)

        menu.addSeparator()

        act_delete = Action(FIF.DELETE, "Xóa video", self)
        act_delete.triggered.connect(lambda: self._on_delete_video(video))
        menu.addAction(act_delete)

        menu.exec(self.table.viewport().mapToGlobal(pos))

