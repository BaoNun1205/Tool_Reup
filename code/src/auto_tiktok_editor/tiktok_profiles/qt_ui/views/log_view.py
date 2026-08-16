"""Live Log Console View."""

from __future__ import annotations

import logging
from datetime import datetime
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)


class LogView(QWidget):
    """Real-time console displaying logs and system events."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_lines = 3000
        self._logs_buffer: list[tuple[str, str, str]] = []  # time, level, msg

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Header & Actions
        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("Nhật Ký Hoạt Động (Live Console)", self))
        header.addStretch(1)

        self.search_edit = LineEdit(self)
        self.search_edit.setPlaceholderText("Lọc từ khóa trong log...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(220)
        self.search_edit.textChanged.connect(self._rebuild_text)
        header.addWidget(self.search_edit)

        self.level_combo = ComboBox(self)
        self.level_combo.addItems(["Tất cả cấp độ", "INFO", "WARNING", "ERROR"])
        self.level_combo.currentIndexChanged.connect(self._rebuild_text)
        header.addWidget(self.level_combo)

        self.chk_autoscroll = CheckBox("Tự động cuộn xuống", self)
        self.chk_autoscroll.setChecked(True)
        header.addWidget(self.chk_autoscroll)

        self.clear_btn = PushButton("Xóa Log", self, FIF.DELETE)
        self.clear_btn.clicked.connect(self.clear_logs)
        header.addWidget(self.clear_btn)

        self.copy_btn = PushButton("Sao chép", self, FIF.COPY)
        self.copy_btn.clicked.connect(self._copy_all)
        header.addWidget(self.copy_btn)

        self.export_btn = PushButton("Xuất file", self, FIF.SAVE)
        self.export_btn.clicked.connect(self._export_to_file)
        header.addWidget(self.export_btn)

        layout.addLayout(header)

        # Console Text Box
        self.console = QPlainTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(self._max_lines)
        layout.addWidget(self.console, 1)

        self.apply_theme_mode()

    def apply_theme_mode(self, mode: str | None = None) -> None:
        from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode
        m = (mode or get_current_theme_mode()).lower()
        if m == "dark":
            self.console.setStyleSheet("""
                QPlainTextEdit {
                    background-color: #171A23;
                    color: #F3F4F8;
                    border: 1px solid #303543;
                    border-radius: 10px;
                    font-family: 'Consolas', 'Cascadia Code', monospace;
                    font-size: 12px;
                    padding: 14px;
                }
            """)
        else:
            self.console.setStyleSheet("""
                QPlainTextEdit {
                    background-color: #FFFFFF;
                    color: #181B2A;
                    border: 1px solid #E4E7EF;
                    border-radius: 10px;
                    font-family: 'Consolas', 'Cascadia Code', monospace;
                    font-size: 12px;
                    padding: 14px;
                }
            """)

    @Slot(str, str, str)
    def append_log(self, timestamp: str, level: str, message: str) -> None:
        self._logs_buffer.append((timestamp, level, message))
        if len(self._logs_buffer) > self._max_lines:
            self._logs_buffer = self._logs_buffer[-self._max_lines:]

        # Filter check
        selected_level = self.level_combo.currentText()
        if selected_level != "Tất cả cấp độ" and level.upper() != selected_level:
            return

        query = self.search_edit.text().strip().lower()
        if query and query not in message.lower():
            return

        formatted = f"[{timestamp}] [{level.upper()}] {message}"
        self.console.appendPlainText(formatted)

        if self.chk_autoscroll.isChecked():
            self.console.moveCursor(QTextCursor.MoveOperation.End)

    def _rebuild_text(self) -> None:
        selected_level = self.level_combo.currentText()
        query = self.search_edit.text().strip().lower()

        lines = []
        for ts, lvl, msg in self._logs_buffer:
            if selected_level != "Tất cả cấp độ" and lvl.upper() != selected_level:
                continue
            if query and query not in msg.lower():
                continue
            lines.append(f"[{ts}] [{lvl.upper()}] {msg}")

        self.console.setPlainText("\n".join(lines))
        if self.chk_autoscroll.isChecked():
            self.console.moveCursor(QTextCursor.MoveOperation.End)

    def clear_logs(self) -> None:
        self._logs_buffer.clear()
        self.console.clear()

    def _copy_all(self) -> None:
        text = self.console.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            InfoBar.success("Đã sao chép", "Đã sao chép toàn bộ log vào Clipboard.", parent=self.window())

    def _export_to_file(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Xuất File Nhật Ký Log",
            f"tiktok_profile_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text files (*.txt);;All files (*.*)",
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(self.console.toPlainText())
                InfoBar.success("Đã xuất", f"Đã lưu log vào: {file_path}", parent=self.window())
            except Exception as exc:
                InfoBar.error("Lỗi xuất file", str(exc), parent=self.window())
