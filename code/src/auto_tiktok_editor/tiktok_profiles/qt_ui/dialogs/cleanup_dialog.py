"""Storage Cleanup Dialog with granular item selection and real-time size computation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    IndeterminateProgressRing,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SingleDirectionScrollArea,
    SmoothScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    ToolButton,
)

from auto_tiktok_editor.app.media_cleanup import (
    CleanupItemInfo,
    scan_cleanup_items,
    _human_size,
)
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import WorkerThread


class CleanupItemRow(QWidget):
    """Row widget for a single cleanable item with CheckBox, Title, Description, and Size badge."""

    def __init__(
        self,
        item: CleanupItemInfo,
        parent: QWidget | None = None,
        on_change: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.on_change = on_change

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        # Checkbox
        self.checkbox = CheckBox(self)
        self.checkbox.setChecked(item.default_checked)
        if self.on_change:
            self.checkbox.stateChanged.connect(lambda: self.on_change())
        layout.addWidget(self.checkbox)

        # Text Info (Title + Description)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title_lbl = BodyLabel(item.title, self)
        self.title_lbl.setStyleSheet("font-weight: 600;")
        title_row.addWidget(self.title_lbl)

        if item.warning_note:
            warn_lbl = CaptionLabel(f"⚠️ {item.warning_note}", self)
            warn_lbl.setStyleSheet("color: #E67E22; font-weight: 500;")
            title_row.addWidget(warn_lbl)

        title_row.addStretch(1)
        text_layout.addLayout(title_row)

        desc_lbl = CaptionLabel(item.description, self)
        desc_lbl.setStyleSheet("color: #7F8596;")
        text_layout.addWidget(desc_lbl)

        layout.addLayout(text_layout, 1)

        # Size and File Count Badge
        size_layout = QVBoxLayout()
        size_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        size_layout.setSpacing(1)

        size_text = _human_size(item.size_bytes)
        self.size_lbl = BodyLabel(size_text, self)
        self.size_lbl.setStyleSheet("font-weight: bold; color: #6D5DFB;")
        size_layout.addWidget(self.size_lbl, 0, Qt.AlignmentFlag.AlignRight)

        count_text = f"{item.file_count} tệp" if item.file_count > 0 else "0 tệp"
        count_lbl = CaptionLabel(count_text, self)
        count_lbl.setStyleSheet("color: #8C8C8C;")
        size_layout.addWidget(count_lbl, 0, Qt.AlignmentFlag.AlignRight)

        layout.addLayout(size_layout)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)


class CleanupProgressDialog(MessageBoxBase):
    """Non-dismissible progress dialog shown while files are being removed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.widget.setFixedWidth(390)
        self.buttonGroup.hide()

        self.titleLabel = SubtitleLabel("Đang dọn dẹp hệ thống", self)
        self.viewLayout.addWidget(self.titleLabel)

        self.progress_ring = IndeterminateProgressRing(self)
        self.progress_ring.setFixedSize(44, 44)
        self.progress_ring.setStrokeWidth(4)
        self.viewLayout.addWidget(self.progress_ring, 0, Qt.AlignmentFlag.AlignHCenter)

        self.status_label = CaptionLabel(
            "Đang xóa dữ liệu đã chọn. Vui lòng không đóng ứng dụng.", self
        )
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #7F8596;")
        self.viewLayout.addWidget(self.status_label)

    def reject(self) -> None:
        """Cleanup cannot be safely cancelled after it has started."""
        return

    def finish(self) -> None:
        self.progress_ring.stop()
        self.accept()


class CleanupDialog(MessageBoxBase):
    """Fluent dialog for granular storage cleanup with categorised checkboxes and live size preview."""

    def __init__(
        self,
        parent: QWidget | None = None,
        config: Optional[PipelineConfig] = None,
        project_root: Optional[Path | str] = None,
        phone_controller: Optional[object] = None,
        manager: Optional[object] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config or PipelineConfig.from_env()
        self.project_root = Path(project_root or getattr(manager, "project_root", ".")).expanduser().resolve()
        self.phone_controller = phone_controller
        self.manager = manager
        self.confirmed = False
        self.item_rows: Dict[str, CleanupItemRow] = {}
        self._scan_worker: WorkerThread | None = None

        self.widget.setMinimumWidth(640)
        self.widget.setMaximumWidth(720)

        # Header Title
        header_row = QHBoxLayout()
        self.titleLabel = SubtitleLabel("🧹 Dọn dẹp Hệ thống & Giải phóng Dung lượng", self)
        header_row.addWidget(self.titleLabel)
        header_row.addStretch(1)

        self.btn_rescan = ToolButton(FIF.SYNC, self)
        self.btn_rescan.setToolTip("Quét lại dung lượng thực tế")
        self.btn_rescan.clicked.connect(self._rescan_items)
        header_row.addWidget(self.btn_rescan)
        self.viewLayout.addLayout(header_row)

        self.subtitleLabel = CaptionLabel(
            "Tùy chọn từng hạng mục cần dọn dẹp. Cookie và tài khoản đăng nhập luôn được bảo vệ an toàn.",
            self,
        )
        self.subtitleLabel.setStyleSheet("color: #7F8596; margin-bottom: 8px;")
        self.viewLayout.addWidget(self.subtitleLabel)

        # Scroll area for cleanable item categories
        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setMinimumHeight(320)
        scroll.setMaximumHeight(420)

        self.content_container = QWidget(scroll)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(2, 4, 2, 4)
        self.content_layout.setSpacing(12)
        scroll.setWidget(self.content_container)

        self.viewLayout.addWidget(scroll)

        # Bottom Summary Card
        summary_card = CardWidget(self)
        summary_layout = QHBoxLayout(summary_card)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        summary_layout.setSpacing(8)

        self.summary_label = StrongBodyLabel("Đang quét dung lượng...", summary_card)
        self.summary_label.setStyleSheet("color: #6D5DFB;")
        summary_layout.addWidget(self.summary_label)

        self.scan_ring = IndeterminateProgressRing(summary_card, start=False)
        self.scan_ring.setFixedSize(20, 20)
        self.scan_ring.setStrokeWidth(3)
        self.scan_ring.hide()
        summary_layout.addWidget(self.scan_ring)
        summary_layout.addStretch(1)

        self.btn_select_safe = PushButton("Chọn mục an toàn", summary_card, FIF.COMPLETED)
        self.btn_select_safe.clicked.connect(self._select_safe_only)
        summary_layout.addWidget(self.btn_select_safe)

        self.btn_select_none = PushButton("Bỏ chọn hết", summary_card, FIF.CLOSE)
        self.btn_select_none.clicked.connect(self._select_none)
        summary_layout.addWidget(self.btn_select_none)

        self.viewLayout.addWidget(summary_card)

        # Buttons
        self.yesButton.setText("Tiến hành Dọn dẹp")
        self.cancelButton.setText("Hủy bỏ")

        # Let the dialog render first, then scan recursively in a worker thread.
        self._set_scanning_state()
        QTimer.singleShot(0, self._load_and_render_items)

    def _load_and_render_items(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return

        self._set_scanning_state()

        def _scan() -> List[CleanupItemInfo]:
            return scan_cleanup_items(self.config, self.project_root, self.phone_controller)

        worker = WorkerThread(_scan, parent=self)
        self._scan_worker = worker
        worker.finished_task.connect(self._render_scanned_items)
        worker.error_task.connect(self._on_scan_error)
        worker.start()

    def _set_scanning_state(self) -> None:
        self.summary_label.setText("Đang quét dung lượng, vui lòng chờ...")
        self.summary_label.setStyleSheet("color: #6D5DFB;")
        self.scan_ring.show()
        self.scan_ring.start()
        self.yesButton.setEnabled(False)
        self.btn_rescan.setEnabled(False)
        self.btn_select_safe.setEnabled(False)
        self.btn_select_none.setEnabled(False)

    def _render_scanned_items(self, items: List[CleanupItemInfo]) -> None:
        self.scan_ring.stop()
        self.scan_ring.hide()
        # Clear existing rows
        for i in reversed(range(self.content_layout.count())):
            widget = self.content_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.item_rows.clear()

        # Group items
        safe_items = [it for it in items if it.group == "safe"]
        media_items = [it for it in items if it.group == "media"]
        dev_items = [it for it in items if it.group == "dev"]

        # Render Group 1: Safe
        if safe_items:
            card_safe = CardWidget(self.content_container)
            l_safe = QVBoxLayout(card_safe)
            l_safe.setContentsMargins(12, 10, 12, 10)
            l_safe.setSpacing(4)

            grp_title = StrongBodyLabel("🟢 Dọn dẹp Định kỳ (An toàn 100%)", card_safe)
            grp_title.setStyleSheet("color: #27AE60; font-weight: bold;")
            l_safe.addWidget(grp_title)

            for item in safe_items:
                row = CleanupItemRow(item, card_safe, on_change=self._update_summary)
                self.item_rows[item.key] = row
                l_safe.addWidget(row)

            self.content_layout.addWidget(card_safe)

        # Render Group 2: Media
        if media_items:
            card_media = CardWidget(self.content_container)
            l_media = QVBoxLayout(card_media)
            l_media.setContentsMargins(12, 10, 12, 10)
            l_media.setSpacing(4)

            grp_title = StrongBodyLabel("🟡 Quản lý Video & Hàng đợi", card_media)
            grp_title.setStyleSheet("color: #E67E22; font-weight: bold;")
            l_media.addWidget(grp_title)

            for item in media_items:
                row = CleanupItemRow(item, card_media, on_change=self._update_summary)
                self.item_rows[item.key] = row
                l_media.addWidget(row)

            self.content_layout.addWidget(card_media)

        # Render Group 3: Dev / Backups
        if dev_items:
            card_dev = CardWidget(self.content_container)
            l_dev = QVBoxLayout(card_dev)
            l_dev.setContentsMargins(12, 10, 12, 10)
            l_dev.setSpacing(4)

            grp_title = StrongBodyLabel("⚙️ Dọn dẹp Nâng cao (Build & Backup)", card_dev)
            grp_title.setStyleSheet("color: #3498DB; font-weight: bold;")
            l_dev.addWidget(grp_title)

            for item in dev_items:
                row = CleanupItemRow(item, card_dev, on_change=self._update_summary)
                self.item_rows[item.key] = row
                l_dev.addWidget(row)

            self.content_layout.addWidget(card_dev)

        self._update_summary()
        self.btn_rescan.setEnabled(True)
        self.btn_select_safe.setEnabled(True)
        self.btn_select_none.setEnabled(True)
        self._scan_worker = None

    def _on_scan_error(self, exc: Exception, _traceback: str) -> None:
        self._scan_worker = None
        self.scan_ring.stop()
        self.scan_ring.hide()
        self.summary_label.setText("Không thể quét dung lượng.")
        self.summary_label.setStyleSheet("color: #C42B1C;")
        self.btn_rescan.setEnabled(True)
        InfoBar.error("Lỗi quét dữ liệu", str(exc), parent=self.window())

    def _update_summary(self) -> None:
        selected_count = 0
        total_freed_bytes = 0
        for row in self.item_rows.values():
            if row.is_checked():
                selected_count += 1
                total_freed_bytes += row.item.size_bytes

        if selected_count == 0:
            self.summary_label.setText("Chưa chọn mục nào để dọn dẹp.")
            self.summary_label.setStyleSheet("color: #8C8C8C;")
            self.yesButton.setEnabled(False)
        else:
            size_str = _human_size(total_freed_bytes)
            self.summary_label.setText(f"Dung lượng dự kiến giải phóng: {size_str} ({selected_count} mục)")
            self.summary_label.setStyleSheet("color: #6D5DFB; font-weight: bold;")
            self.yesButton.setEnabled(True)

    def _select_safe_only(self) -> None:
        for row in self.item_rows.values():
            row.set_checked(row.item.group == "safe")
        self._update_summary()

    def _select_none(self) -> None:
        for row in self.item_rows.values():
            row.set_checked(False)
        self._update_summary()

    def _rescan_items(self) -> None:
        self._load_and_render_items()

    def get_selected_keys(self) -> List[str]:
        return [key for key, row in self.item_rows.items() if row.is_checked()]

    def validate(self) -> bool:
        selected = self.get_selected_keys()
        if not selected:
            InfoBar.warning("Chưa chọn", "Vui lòng tích chọn ít nhất một hạng mục để dọn dẹp.", parent=self.window())
            return False
        self.confirmed = True
        return True
