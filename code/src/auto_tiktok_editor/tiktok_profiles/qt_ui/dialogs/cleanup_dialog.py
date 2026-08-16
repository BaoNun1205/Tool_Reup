"""Storage Cleanup Confirmation Dialog."""

from __future__ import annotations

from typing import Any
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    MessageBoxBase,
    PlainTextEdit,
    SubtitleLabel,
    BodyLabel,
    PrimaryPushButton,
    PushButton,
)


class CleanupDialog(MessageBoxBase):
    """Fluent dialog displaying storage report and asking for cleanup confirmation."""

    def __init__(
        self,
        parent: QWidget | None = None,
        report_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.confirmed = False

        self.titleLabel = SubtitleLabel("Dọn dẹp bộ nhớ & file tạm", self)
        self.viewLayout.addWidget(self.titleLabel)

        self.bodyLabel = BodyLabel("Chi tiết dung lượng các thư mục tạm và cache:", self)
        self.viewLayout.addWidget(self.bodyLabel)

        self.report_edit = PlainTextEdit(self)
        self.report_edit.setPlainText(report_text)
        self.report_edit.setReadOnly(True)
        self.report_edit.setMinimumHeight(180)
        self.viewLayout.addWidget(self.report_edit)

        self.yesButton.setText("Tiến hành dọn dẹp")
        self.cancelButton.setText("Đóng")
        self.widget.setMinimumWidth(500)

    def validate(self) -> bool:
        self.confirmed = True
        return True
