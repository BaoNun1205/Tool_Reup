"""Schedule & DateTime Dialog for video publishing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget, QFileDialog
from qfluentwidgets import (
    CalendarPicker,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    SegmentedWidget,
    SubtitleLabel,
    BodyLabel,
    TimePicker,
)

from auto_tiktok_editor.tiktok_profiles.models import PUBLISH_MODES
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import default_schedule_time_text, parse_schedule_time


class ScheduleDialog(MessageBoxBase):
    """Fluent dialog to configure publication mode and schedule time."""

    def __init__(
        self,
        parent: QWidget | None = None,
        video: Any = None,
        initial_publish_mode: str = "now",
        initial_time: str = "",
        initial_product_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.video = video
        self.result_data: dict[str, Any] | None = None

        self.titleLabel = SubtitleLabel("Thiết lập lịch đăng video", self)
        self.viewLayout.addWidget(self.titleLabel)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 16, 0, 16)
        form_layout.setSpacing(12)

        # Publish mode selector
        self.mode_combo = ComboBox(self)
        for mode in PUBLISH_MODES:
            self.mode_combo.addItem(mode)
        mode_val = getattr(video, "publish_mode", initial_publish_mode) or "now"
        if mode_val in PUBLISH_MODES:
            self.mode_combo.setCurrentText(mode_val)
        form_layout.addRow(BodyLabel("Chế độ đăng:", self), self.mode_combo)

        # Product ID
        self.product_id_edit = LineEdit(self)
        self.product_id_edit.setPlaceholderText("Mã sản phẩm TikTok Shop (nếu có)")
        prod_val = getattr(video, "product_id", initial_product_id) or ""
        self.product_id_edit.setText(prod_val)
        form_layout.addRow(BodyLabel("Product ID:", self), self.product_id_edit)

        # Date Picker & Time Picker
        time_text = getattr(video, "scheduled_at", initial_time) or default_schedule_time_text()
        dt = parse_schedule_time(time_text) or (datetime.now() + timedelta(minutes=30))

        date_time_layout = QHBoxLayout()
        date_time_layout.setSpacing(8)

        self.calendar_picker = CalendarPicker(self)
        self.calendar_picker.setDate(QDate(dt.year, dt.month, dt.day))
        date_time_layout.addWidget(self.calendar_picker, 1)

        self.time_picker = TimePicker(self)
        self.time_picker.setTime(QTime(dt.hour, dt.minute))
        date_time_layout.addWidget(self.time_picker, 1)

        form_layout.addRow(BodyLabel("Thời gian hẹn đăng:", self), date_time_layout)

        self.viewLayout.addLayout(form_layout)
        self.yesButton.setText("Lưu lịch")
        self.cancelButton.setText("Hủy")
        self.widget.setMinimumWidth(450)

    def validate(self) -> bool:
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
        mode = self.mode_combo.currentText()

        self.result_data = {
            "publish_mode": mode,
            "scheduled_at": scheduled_at_str,
            "product_id": self.product_id_edit.text().strip(),
        }
        return True
