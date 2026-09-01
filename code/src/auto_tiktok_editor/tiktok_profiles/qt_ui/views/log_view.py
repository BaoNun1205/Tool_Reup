"""Logs View: Real-time console and structured table for system logs and events."""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidgetItem,
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
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)
from qfluentwidgets.common.smooth_scroll import SmoothMode

from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    format_vietnam_datetime,
    get_current_theme_mode,
)


class LogView(QWidget):
    """Modern Logs View with both Structured Table and Developer Terminal Console."""

    def __init__(
        self,
        manager: TikTokProfileManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self._max_lines = 3000
        self._logs_cache: list[dict[str, Any]] = []

        self._init_ui()
        self.refresh_logs()

        # Auto-sync timer every 2.5 seconds
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(2500)
        self._sync_timer.timeout.connect(self._sync_logs_live)
        self._sync_timer.start()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(14)

        # Header Row: Title + Segmented View Switcher + Actions
        header_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Logs", self)
        header_layout.addWidget(self.title_label)

        # View Switcher (Table / Terminal)
        self.view_switcher = SegmentedWidget(self)
        self.view_switcher.addItem("table", "Bảng dữ liệu (Table)", onClick=lambda: self.stack.setCurrentIndex(0))
        self.view_switcher.addItem("console", "Dòng lệnh (Console)", onClick=lambda: self.stack.setCurrentIndex(1))
        self.view_switcher.setCurrentItem("table")
        header_layout.addWidget(self.view_switcher)

        header_layout.addStretch(1)

        # Toolbar Filter Controls
        self.search_edit = LineEdit(self)
        self.search_edit.setPlaceholderText("Lọc từ khóa trong log...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(200)
        self.search_edit.textChanged.connect(self._apply_filters)
        header_layout.addWidget(self.search_edit)

        self.level_combo = ComboBox(self)
        self.level_combo.addItems(["Tất cả cấp độ", "INFO", "WARNING", "ERROR"])
        self.level_combo.setFixedWidth(130)
        self.level_combo.currentIndexChanged.connect(self._apply_filters)
        header_layout.addWidget(self.level_combo)

        self.action_combo = ComboBox(self)
        self.action_combo.addItem("Tất cả hành động")
        self.action_combo.setFixedWidth(160)
        self.action_combo.currentIndexChanged.connect(self._apply_filters)
        header_layout.addWidget(self.action_combo)

        self.chk_autoscroll = CheckBox("Tự cuộn", self)
        self.chk_autoscroll.setChecked(True)
        header_layout.addWidget(self.chk_autoscroll)

        self.refresh_btn = PushButton("Làm mới", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_logs)
        header_layout.addWidget(self.refresh_btn)

        self.clear_btn = PushButton("Xóa Log", self, FIF.DELETE)
        self.clear_btn.clicked.connect(self._confirm_clear_logs)
        header_layout.addWidget(self.clear_btn)

        self.copy_btn = PushButton("Sao chép", self, FIF.COPY)
        self.copy_btn.clicked.connect(self._copy_all)
        header_layout.addWidget(self.copy_btn)

        self.export_btn = PushButton("Xuất file", self, FIF.SAVE)
        self.export_btn.clicked.connect(self._export_to_file)
        header_layout.addWidget(self.export_btn)

        main_layout.addLayout(header_layout)

        # Stacked Widget: Table View (Index 0) & Console View (Index 1)
        self.stack = QStackedWidget(self)

        # Page 0: Structured Table
        self.table = TableWidget(self.stack)
        if hasattr(self.table, "scrollDelagate") and hasattr(self.table.scrollDelagate, "verticalSmoothScroll"):
            self.table.scrollDelagate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
            self.table.scrollDelagate.horizonSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Thời gian",
            "Mức độ",
            "Hành động",
            "Profile / Video",
            "Nội dung chi tiết",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(38)

        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 140)

        self.stack.addWidget(self.table)

        # Page 1: Live Terminal Console
        self.console = QPlainTextEdit(self.stack)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(self._max_lines)
        self.stack.addWidget(self.console)

        main_layout.addWidget(self.stack, 1)

        self.apply_theme_mode()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_logs()
        if hasattr(self, "_sync_timer") and not self._sync_timer.isActive():
            self._sync_timer.start()

    def apply_theme_mode(self, mode: str | None = None) -> None:
        m = (mode or get_current_theme_mode()).lower()
        if m == "dark":
            self.console.setStyleSheet("""
                QPlainTextEdit {
                    background-color: #12141C;
                    color: #E6E8F0;
                    border: 1px solid #282C3A;
                    border-radius: 10px;
                    font-family: 'Consolas', 'Cascadia Code', monospace;
                    font-size: 12px;
                    padding: 14px;
                    line-height: 1.5;
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
                    line-height: 1.5;
                }
            """)
        self._apply_filters()

    def refresh_logs(self) -> None:
        """Fetch all logs from SQLite database and rebuild view."""
        raw_items: list[dict[str, Any]] = []

        if self.manager:
            try:
                db_logs = self.manager.list_logs(limit=self._max_lines)
                accounts_map = {a.id: a.name for a in self.manager.list_accounts()}
                for item in db_logs:
                    acc_id = getattr(item, "account_id", None)
                    vid_id = getattr(item, "video_id", None)
                    target_parts = []
                    if acc_id:
                        target_parts.append(accounts_map.get(acc_id, f"Acc #{acc_id}"))
                    if vid_id:
                        target_parts.append(f"Vid #{vid_id}")
                    target_str = " | ".join(target_parts) if target_parts else "-"

                    raw_items.append({
                        "id": getattr(item, "id", 0),
                        "timestamp": format_vietnam_datetime(getattr(item, "created_at", None)),
                        "level": str(getattr(item, "level", "info") or "info").upper(),
                        "action": str(getattr(item, "action", "") or "system"),
                        "target": target_str,
                        "message": str(getattr(item, "message", "") or ""),
                    })
            except Exception:
                pass

        self._logs_cache = raw_items
        self._update_action_combobox()
        self._apply_filters()

    def _sync_logs_live(self) -> None:
        """Lightweight sync for new incoming SQLite logs."""
        if not self.manager:
            return
        try:
            latest = self.manager.list_logs(limit=10)
            if not latest:
                return
            latest_id = getattr(latest[0], "id", 0)
            cached_latest_id = self._logs_cache[0].get("id", 0) if self._logs_cache else 0
            if latest_id != cached_latest_id or len(latest) != len(self._logs_cache[:10]):
                self.refresh_logs()
        except Exception:
            pass

    def _update_action_combobox(self) -> None:
        current_action = self.action_combo.currentText()
        actions = sorted({item["action"] for item in self._logs_cache if item["action"]})

        self.action_combo.blockSignals(True)
        self.action_combo.clear()
        self.action_combo.addItem("Tất cả hành động")
        for act in actions:
            self.action_combo.addItem(act)

        idx = self.action_combo.findText(current_action)
        if idx >= 0:
            self.action_combo.setCurrentIndex(idx)
        else:
            self.action_combo.setCurrentIndex(0)
        self.action_combo.blockSignals(False)

    @Slot(str, str, str)
    def append_log(self, timestamp: str, level: str, message: str, action: str = "system") -> None:
        """Slot for live logging bridge signals."""
        entry = {
            "id": 0,
            "timestamp": timestamp or format_vietnam_datetime(datetime.now()),
            "level": str(level or "INFO").upper(),
            "action": action,
            "target": "-",
            "message": message,
        }
        self._logs_cache.insert(0, entry)
        if len(self._logs_cache) > self._max_lines:
            self._logs_cache.pop()
        self._apply_filters()

    def _apply_filters(self) -> None:
        selected_level = self.level_combo.currentText()
        selected_action = self.action_combo.currentText()
        query = self.search_edit.text().strip().lower()

        filtered = []
        for log_entry in self._logs_cache:
            lvl = log_entry["level"]
            act = log_entry["action"]
            msg = log_entry["message"]
            tgt = log_entry["target"]

            if selected_level != "Tất cả cấp độ" and lvl != selected_level:
                continue

            if selected_action != "Tất cả hành động" and act != selected_action:
                continue

            if query and not (query in msg.lower() or query in act.lower() or query in tgt.lower() or query in lvl.lower()):
                continue

            filtered.append(log_entry)

        # 1. Update Table
        self.table.blockSignals(True)
        self.table.setRowCount(len(filtered))

        for row, entry in enumerate(filtered):
            # Col 0: Timestamp
            ts_item = QTableWidgetItem(entry["timestamp"])
            ts_item.setForeground(QColor("#7F8596"))
            self.table.setItem(row, 0, ts_item)

            # Col 1: Level with LED / Color
            lvl_str = entry["level"]
            lvl_item = QTableWidgetItem(f"● {lvl_str}")
            font = lvl_item.font()
            font.setBold(True)
            lvl_item.setFont(font)
            if lvl_str == "ERROR":
                lvl_item.setForeground(QColor("#E24A4A"))
            elif lvl_str == "WARNING":
                lvl_item.setForeground(QColor("#F59E0B"))
            else:
                lvl_item.setForeground(QColor("#3B82F6"))
            self.table.setItem(row, 1, lvl_item)

            # Col 2: Action
            act_item = QTableWidgetItem(entry["action"])
            self.table.setItem(row, 2, act_item)

            # Col 3: Target Profile / Video
            tgt_item = QTableWidgetItem(entry["target"])
            tgt_item.setForeground(QColor("#7F8596"))
            self.table.setItem(row, 3, tgt_item)

            # Col 4: Message
            msg_item = QTableWidgetItem(entry["message"])
            msg_item.setToolTip(entry["message"])
            self.table.setItem(row, 4, msg_item)

        self.table.blockSignals(False)

        # 2. Update Terminal Console (Chronological order: Oldest to Newest)
        console_lines = []
        for entry in reversed(filtered):
            console_lines.append(f"[{entry['timestamp']}] [{entry['level']:7s}] [{entry['action']}] {entry['message']}")

        self.console.setPlainText("\n".join(console_lines))
        if self.chk_autoscroll.isChecked():
            self.console.moveCursor(QTextCursor.MoveOperation.End)

    def _confirm_clear_logs(self) -> None:
        mb = MessageBox("Xóa toàn bộ Logs", "Bạn có chắc chắn muốn xóa toàn bộ lịch sử nhật ký hệ thống?", self.window())
        if mb.exec():
            if self.manager:
                try:
                    self.manager.clear_logs()
                except Exception:
                    pass
            self._logs_cache.clear()
            self.table.setRowCount(0)
            self.console.clear()
            InfoBar.success("Đã xóa", "Đã dọn dẹp sạch nhật ký hệ thống.", position=InfoBarPosition.TOP, parent=self.window())

    def _copy_all(self) -> None:
        if self.stack.currentIndex() == 0:
            # Copy table content
            rows_data = []
            for r in range(self.table.rowCount()):
                row_str = "\t".join(self.table.item(r, c).text() if self.table.item(r, c) else "" for c in range(self.table.columnCount()))
                rows_data.append(row_str)
            text = "\n".join(rows_data)
        else:
            text = self.console.toPlainText()

        if text:
            QApplication.clipboard().setText(text)
            InfoBar.success("Đã sao chép", "Đã sao chép nhật ký vào Clipboard.", position=InfoBarPosition.TOP, duration=2000, parent=self.window())

    def _export_to_file(self) -> None:
        if not self._logs_cache:
            InfoBar.warning("Không có dữ liệu", "Không có nhật ký nào để xuất file!", position=InfoBarPosition.TOP, parent=self.window())
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Xuất File Nhật Ký",
            f"system_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;CSV Files (*.csv)",
        )
        if not file_path:
            return

        try:
            if file_path.endswith(".csv"):
                with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Thời gian", "Mức độ", "Hành động", "Target", "Chi tiết"])
                    for entry in self._logs_cache:
                        writer.writerow([entry["timestamp"], entry["level"], entry["action"], entry["target"], entry["message"]])
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    for entry in reversed(self._logs_cache):
                        f.write(f"[{entry['timestamp']}] [{entry['level']}] [{entry['action']}] {entry['message']}\n")

            InfoBar.success("Xuất file thành công", f"Đã lưu nhật ký vào: {file_path}", position=InfoBarPosition.TOP, parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi xuất file", str(exc), position=InfoBarPosition.TOP, parent=self.window())

    def shutdown(self) -> None:
        if hasattr(self, "_sync_timer"):
            self._sync_timer.stop()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
