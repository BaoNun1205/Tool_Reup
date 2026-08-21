"""Video Dialog for adding / editing a video item."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CalendarPicker,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    BodyLabel,
    TimePicker,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
)

from auto_tiktok_editor.tiktok_profiles.models import PUBLISH_MODES
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import default_schedule_time_text, parse_schedule_time


class VideoDialog(MessageBoxBase):
    """Fluent dialog to add or edit a video with file picker and schedule."""

    def __init__(
        self,
        parent: QWidget | None = None,
        file_path: str = "",
        video: Any = None,
        is_edit: bool = False,
    ) -> None:
        super().__init__(parent)
        self.is_edit = is_edit
        self.video = video
        self.result_data: dict[str, Any] | None = None

        title_text = "Chỉnh sửa video" if is_edit else "Thêm video mới vào tài khoản"
        self.titleLabel = SubtitleLabel(title_text, self)
        self.viewLayout.addWidget(self.titleLabel)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 16, 0, 16)
        form_layout.setSpacing(12)

        # Video File Row
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.file_edit = LineEdit(self)
        self.file_edit.setPlaceholderText("Đường dẫn file video (.mp4, .mov...)")
        init_file = getattr(video, "file_path", file_path) or file_path
        self.file_edit.setText(init_file)
        file_row.addWidget(self.file_edit, 1)

        self.browse_btn = PushButton("Chọn file", self, FIF.FOLDER)
        self.browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(self.browse_btn)
        form_layout.addRow(BodyLabel("File Video:", self), file_row)

        # Caption
        self.caption_edit = LineEdit(self)
        self.caption_edit.setPlaceholderText("Mô tả / Caption video...")
        if video:
            self.caption_edit.setText(getattr(video, "caption", "") or "")
        form_layout.addRow(BodyLabel("Caption:", self), self.caption_edit)

        # Hashtags
        self.hashtags_edit = LineEdit(self)
        self.hashtags_edit.setPlaceholderText("#xuhuong #fyp #affiliate...")
        if video:
            self.hashtags_edit.setText(getattr(video, "hashtags", "") or "")
        form_layout.addRow(BodyLabel("Hashtags:", self), self.hashtags_edit)

        # Product ID
        self.product_id_edit = LineEdit(self)
        self.product_id_edit.setPlaceholderText("Mã sản phẩm TikTok Shop (tùy chọn)")
        if video:
            self.product_id_edit.setText(getattr(video, "product_id", "") or "")
        form_layout.addRow(BodyLabel("Product ID:", self), self.product_id_edit)

        # Publish Mode & Schedule Time
        self.mode_combo = ComboBox(self)
        for m in PUBLISH_MODES:
            self.mode_combo.addItem(m)
        init_mode = getattr(video, "publish_mode", "now") or "now"
        self.mode_combo.setCurrentText(init_mode if init_mode in PUBLISH_MODES else "now")

        time_text = getattr(video, "scheduled_at", "") or default_schedule_time_text()
        dt = parse_schedule_time(time_text) or (datetime.now() + timedelta(minutes=30))

        schedule_row = QHBoxLayout()
        schedule_row.setSpacing(8)
        schedule_row.addWidget(self.mode_combo, 1)

        self.calendar_picker = CalendarPicker(self)
        self.calendar_picker.setDate(QDate(dt.year, dt.month, dt.day))
        schedule_row.addWidget(self.calendar_picker, 1)

        self.time_picker = TimePicker(self)
        self.time_picker.setTime(QTime(dt.hour, dt.minute))
        schedule_row.addWidget(self.time_picker, 1)
        form_layout.addRow(BodyLabel("Lịch đăng:", self), schedule_row)

        self.viewLayout.addLayout(form_layout)
        self.yesButton.setText("Lưu" if is_edit else "Thêm")
        self.cancelButton.setText("Hủy")
        self.widget.setMinimumWidth(560)

    def _on_browse(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file video",
            "",
            "Video files (*.mp4 *.mov *.m4v *.avi *.mkv);;All files (*.*)",
        )
        if file_path:
            self.file_edit.setText(file_path)

    def validate(self) -> bool:
        file_path = self.file_edit.text().strip()
        if not file_path:
            InfoBar.warning(
                title="Thiếu thông tin",
                content="Vui lòng chọn file video!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return False

        if not Path(file_path).expanduser().exists():
            InfoBar.error(
                title="File không tồn tại",
                content=f"Đường dẫn file video không tồn tại: {file_path}",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return False

        selected_date = self.calendar_picker.getDate()
        selected_time = self.time_picker.getTime()
        dt = datetime(
            selected_date.year(),
            selected_date.month(),
            selected_date.day(),
            selected_time.hour(),
            selected_time.minute(),
        )
        scheduled_at_str = dt.isoformat(sep=" ", timespec="minutes")

        self.result_data = {
            "file_path": file_path,
            "caption": self.caption_edit.text().strip(),
            "hashtags": self.hashtags_edit.text().strip(),
            "product_id": self.product_id_edit.text().strip(),
            "publish_mode": self.mode_combo.currentText(),
            "scheduled_at": scheduled_at_str,
            "note": "",
        }
        return True
