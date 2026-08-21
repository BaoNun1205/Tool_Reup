"""Accounts & Dashboard View for TikTok Profile Manager."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)
from qfluentwidgets.common.smooth_scroll import SmoothMode

from auto_tiktok_editor.tiktok_profiles.models import ACCOUNT_STATUSES
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.stat_card import StatCard
from auto_tiktok_editor.tiktok_profiles.qt_ui.dialogs.account_dialog import AccountDialog
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    VIDEO_ROW_CUT_MODE_LABELS,
    format_account_status,
    format_vietnam_datetime,
)
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import BrowserWorkerThread


class AccountsView(QWidget):
    """Modern Dashboard & Accounts management view."""
    account_selected = Signal(object)
    request_videos_view = Signal(object)
    request_sources_view = Signal(object)

    def __init__(
        self,
        manager: TikTokProfileManager,
        browser_worker: BrowserWorkerThread,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.browser_worker = browser_worker
        self._accounts_cache = []

        self._init_ui()
        self._connect_browser_signals()
        self.refresh_accounts()

    def _connect_browser_signals(self) -> None:
        self.browser_worker.started_task.connect(self._on_browser_started)
        self.browser_worker.finished_task.connect(self._on_browser_finished)
        self.browser_worker.error_task.connect(self._on_browser_error)

    def _on_browser_started(self, message: str) -> None:
        InfoBar.info(
            title="Đang xử lý trình duyệt",
            content=message,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self.window(),
        )

    def _on_browser_finished(self, result: Any, callback: Any) -> None:
        if callable(callback):
            try:
                callback(result)
            except Exception as exc:
                self._on_browser_error(exc, "", "Lỗi cập nhật UI")

    def _on_browser_error(self, exc: Exception, tb: str, error_title: str) -> None:
        InfoBar.error(
            title=error_title,
            content=str(exc),
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=self.window(),
        )

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title
        title_layout = QHBoxLayout()
        self.title_label = SubtitleLabel("Quản lý Tài khoản TikTok Profiles", self)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)

        self.refresh_btn = PushButton("Làm mới", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_accounts)
        title_layout.addWidget(self.refresh_btn)
        main_layout.addLayout(title_layout)

        # Action Toolbar & Filters
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        self.search_edit = LineEdit(self)
        self.search_edit.setPlaceholderText("Tìm kiếm theo tên, tag, bot...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(260)
        self.search_edit.textChanged.connect(self._filter_accounts)
        toolbar_layout.addWidget(self.search_edit)

        self.status_filter = ComboBox(self)
        self.status_filter.addItem("Tất cả trạng thái")
        for status in ACCOUNT_STATUSES:
            self.status_filter.addItem(status)
        self.status_filter.currentIndexChanged.connect(self._filter_accounts)
        toolbar_layout.addWidget(self.status_filter)

        toolbar_layout.addStretch(1)

        self.add_btn = PrimaryPushButton("Thêm tài khoản", self, FIF.ADD)
        self.add_btn.clicked.connect(self._on_add_account)
        toolbar_layout.addWidget(self.add_btn)

        self.edit_defaults_btn = PushButton("Thiết lập mặc định", self, FIF.EDIT)
        self.edit_defaults_btn.setToolTip("Chỉnh Cut Mode và hashtag mặc định của Profile đang chọn")
        self.edit_defaults_btn.clicked.connect(self._on_edit_account)
        toolbar_layout.addWidget(self.edit_defaults_btn)

        self.browser_btn = PushButton("Mở trình duyệt", self, FIF.GLOBE)
        self.browser_btn.clicked.connect(self._on_open_browser)
        toolbar_layout.addWidget(self.browser_btn)

        self.check_cookie_btn = PushButton("Kiểm tra Cookie", self, FIF.COMPLETED)
        self.check_cookie_btn.clicked.connect(self._on_check_cookie)
        toolbar_layout.addWidget(self.check_cookie_btn)

        main_layout.addLayout(toolbar_layout)

        # Accounts Table
        self.table = TableWidget(self)
        if hasattr(self.table, "scrollDelagate") and hasattr(self.table.scrollDelagate, "verticalSmoothScroll"):
            self.table.scrollDelagate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
            self.table.scrollDelagate.horizonSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Tên Profile",
            "Trạng thái",
            "Tên Bot",
            "Đăng nhập",
            "Cut Mode mặc định",
            "Hashtag mặc định",
            "Cập nhật lúc",
            "Ghi chú",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemDoubleClicked.connect(self._on_double_click_row)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        # Set initial column widths
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 145)
        self.table.setColumnWidth(5, 220)
        self.table.setColumnWidth(6, 140)

        main_layout.addWidget(self.table, 1)

    def apply_theme_mode(self, mode: str) -> None:
        """Re-render table cells for active theme."""
        self.refresh_accounts()

    def refresh_accounts(self) -> None:
        """Fetch all accounts from SQLite database and update view."""
        try:
            self._accounts_cache = self.manager.list_accounts()
            self._filter_accounts()
        except Exception as exc:
            InfoBar.error(
                title="Lỗi tải dữ liệu",
                content=f"Không thể đọc danh sách tài khoản: {exc}",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _filter_accounts(self) -> None:
        query = self.search_edit.text().strip().lower()
        selected_status = self.status_filter.currentText()

        filtered = []
        for a in self._accounts_cache:
            name = (getattr(a, "name", "") or "").lower()
            bot = (getattr(a, "bot_name", "") or "").lower()
            tags = (getattr(a, "hashtags", "") or "").lower()
            note = (getattr(a, "note", "") or "").lower()
            status = getattr(a, "status", "") or ""

            if selected_status != "Tất cả trạng thái" and status != selected_status:
                continue

            if query and not (query in name or query in bot or query in tags or query in note):
                continue

            filtered.append(a)

        self._populate_table(filtered)

    def _populate_table(self, accounts: list[Any]) -> None:
        self.table.setRowCount(len(accounts))
        for row, a in enumerate(accounts):
            name_item = QTableWidgetItem(getattr(a, "name", "") or "")
            name_item.setData(Qt.ItemDataRole.UserRole, a)

            status_val = getattr(a, "status", "unknown") or "unknown"
            status_text, status_color = format_account_status(status_val)
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor(status_color))

            bot_item = QTableWidgetItem(getattr(a, "bot_name", "") or "-")
            login_item = QTableWidgetItem(getattr(a, "login_type", "") or "cookie")
            cut_mode = getattr(a, "cut_mode", "") or "original"
            cut_mode_item = QTableWidgetItem(VIDEO_ROW_CUT_MODE_LABELS.get(cut_mode, cut_mode))
            tags_item = QTableWidgetItem(getattr(a, "hashtags", "") or "")
            updated_item = QTableWidgetItem(format_vietnam_datetime(getattr(a, "updated_at", "")))
            note_item = QTableWidgetItem(getattr(a, "note", "") or "")

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, status_item)
            self.table.setItem(row, 2, bot_item)
            self.table.setItem(row, 3, login_item)
            self.table.setItem(row, 4, cut_mode_item)
            self.table.setItem(row, 5, tags_item)
            self.table.setItem(row, 6, updated_item)
            self.table.setItem(row, 7, note_item)


    def get_selected_account(self) -> Any | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection_changed(self) -> None:
        account = self.get_selected_account()
        if account:
            self.account_selected.emit(account)

    def _on_double_click_row(self, item: QTableWidgetItem) -> None:
        self._on_edit_account()

    def _show_context_menu(self, pos: QPoint) -> None:
        account = self.get_selected_account()
        if not account:
            return

        menu = RoundMenu(parent=self)
        act_open_browser = Action(FIF.GLOBE, "Mở TikTok Studio (Playwright)", self)
        act_open_browser.triggered.connect(self._on_open_browser)
        menu.addAction(act_open_browser)

        act_check_cookie = Action(FIF.COMPLETED, "Kiểm tra Cookie đăng nhập", self)
        act_check_cookie.triggered.connect(self._on_check_cookie)
        menu.addAction(act_check_cookie)

        act_mark_live = Action(FIF.ACCEPT, "Đánh dấu Live (Hoạt động)", self)
        act_mark_live.triggered.connect(self._on_mark_live)
        menu.addAction(act_mark_live)

        act_view_videos = Action(FIF.VIDEO, "Xem danh sách Video của Profile", self)
        act_view_videos.triggered.connect(lambda: self.request_videos_view.emit(account))
        menu.addAction(act_view_videos)

        menu.addSeparator()

        act_open_folder = Action(FIF.FOLDER, "Mở thư mục dữ liệu Profile", self)
        act_open_folder.triggered.connect(self._on_open_profile_folder)
        menu.addAction(act_open_folder)

        act_edit = Action(FIF.EDIT, "Chỉnh sửa tài khoản", self)
        act_edit.triggered.connect(self._on_edit_account)
        menu.addAction(act_edit)

        act_delete = Action(FIF.DELETE, "Xóa tài khoản này", self)
        act_delete.triggered.connect(self._on_delete_account)
        menu.addAction(act_delete)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_add_account(self) -> None:
        dialog = AccountDialog(self.window(), is_edit=False)
        if dialog.exec():
            try:
                res = dialog.result
                self.manager.create_account(
                    name=res["name"],
                    bot_name=res.get("bot_name", ""),
                    login_type=res.get("login_type", "google"),
                    cut_mode=res.get("cut_mode", "fixed"),
                    hashtags=res.get("hashtags", ""),
                    profile_path=res.get("profile_path", ""),
                    note=res.get("note", ""),
                )
                InfoBar.success(
                    title="Thành công",
                    content=f"Đã thêm profile '{res['name']}'!",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
                self.refresh_accounts()
            except Exception as exc:
                InfoBar.error(
                    title="Lỗi thêm tài khoản",
                    content=str(exc),
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )

    def _on_edit_account(self) -> None:
        account = self.get_selected_account()
        if not account:
            InfoBar.warning(
                title="Chưa chọn tài khoản",
                content="Vui lòng chọn 1 tài khoản để chỉnh sửa!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        dialog = AccountDialog(self.window(), account=account, is_edit=True)
        if dialog.exec():
            try:
                res = dialog.result
                self.manager.update_account(
                    account_id=account.id,
                    name=res["name"],
                    bot_name=res.get("bot_name", ""),
                    login_type=res.get("login_type", "google"),
                    cut_mode=res.get("cut_mode", "fixed"),
                    hashtags=res.get("hashtags", ""),
                    profile_path=res.get("profile_path", ""),
                    note=res.get("note", ""),
                )
                InfoBar.success(
                    title="Thành công",
                    content=f"Đã cập nhật profile '{res['name']}'!",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
                self.refresh_accounts()
            except Exception as exc:
                InfoBar.error(
                    title="Lỗi cập nhật tài khoản",
                    content=str(exc),
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )

    def _on_delete_account(self) -> None:
        account = self.get_selected_account()
        if not account:
            InfoBar.warning(
                title="Chưa chọn tài khoản",
                content="Vui lòng chọn 1 tài khoản để xóa!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        box = MessageBox(
            title="Xác nhận xóa tài khoản",
            content=f"Bạn có chắc chắn muốn xóa tài khoản '{account.name}' không?",
            parent=self.window(),
        )
        if box.exec():
            try:
                with self.manager._connect() as conn:
                    conn.execute("DELETE FROM accounts WHERE id = ?", (account.id,))
                InfoBar.success(
                    title="Đã xóa",
                    content=f"Đã xóa tài khoản {account.name}",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
                self.refresh_accounts()
            except Exception as exc:
                InfoBar.error(
                    title="Lỗi xóa tài khoản",
                    content=str(exc),
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )

    def _on_open_browser(self) -> None:
        account = self.get_selected_account()
        if not account:
            InfoBar.warning(
                title="Chưa chọn tài khoản",
                content="Vui lòng chọn 1 tài khoản từ bảng trước!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        def _callback(status: str) -> None:
            self.refresh_accounts()
            InfoBar.success(
                title="Trình duyệt đã mở",
                content=f"Đã mở TikTok Studio cho profile '{account.name}' (Trạng thái: {status})",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

        self.browser_worker.open_tiktok_studio(account, callback=_callback)

    def _on_check_cookie(self) -> None:
        account = self.get_selected_account()
        if not account:
            InfoBar.warning(
                title="Chưa chọn tài khoản",
                content="Vui lòng chọn 1 tài khoản từ bảng trước!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return

        def _callback(status: str) -> None:
            self.refresh_accounts()
            if status == "live":
                InfoBar.success(
                    title="Cookie hợp lệ",
                    content=f"Tài khoản '{account.name}' đang hoạt động (Live)!",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
            elif status in ("login", "logged_out"):
                InfoBar.warning(
                    title="Chưa đăng nhập",
                    content=f"Tài khoản '{account.name}' cần đăng nhập lại trên trình duyệt (Trạng thái: {status})!",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )
            else:
                InfoBar.info(
                    title="Trạng thái tài khoản",
                    content=f"Trạng thái của '{account.name}': {status}",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                )

        self.browser_worker.open_tiktok_studio(account, callback=_callback)

    def _on_mark_live(self) -> None:
        account = self.get_selected_account()
        if not account:
            return
        try:
            self.manager.update_status(account.id, "live")
            InfoBar.success(
                title="Đã cập nhật",
                content=f"Đã đánh dấu tài khoản '{account.name}' là Live!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            self.refresh_accounts()
        except Exception as exc:
            InfoBar.error("Lỗi cập nhật", str(exc), parent=self.window())

    def _on_open_profile_folder(self) -> None:
        account = self.get_selected_account()
        if not account:
            return
        try:
            path = self.manager.resolve_profile_path(account)
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            InfoBar.error(
                title="Lỗi mở thư mục",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _show_context_menu(self, pos: QPoint) -> None:
        account = self.get_selected_account()
        if not account:
            return

        menu = RoundMenu(parent=self)

        act_sources = Action(FIF.GLOBE, "🌐 Quản lý Kênh Nguồn (Sources)", self)
        act_sources.triggered.connect(lambda: self.request_sources_view.emit(account.name))
        menu.addAction(act_sources)

        act_videos = Action(FIF.VIDEO, "🎬 Quản lý Video của Profile này", self)
        act_videos.triggered.connect(lambda: self.request_videos_view.emit(account.name))
        menu.addAction(act_videos)

        menu.addSeparator()

        act_browser = Action(FIF.GLOBE, "Mở TikTok Studio (Trình duyệt)", self)
        act_browser.triggered.connect(self._on_open_browser)
        menu.addAction(act_browser)

        act_check = Action(FIF.COMPLETED, "Kiểm tra Cookie", self)
        act_check.triggered.connect(self._on_check_cookie)
        menu.addAction(act_check)

        act_live = Action(FIF.ACCEPT, "Đánh dấu Live", self)
        act_live.triggered.connect(self._on_mark_live)
        menu.addAction(act_live)

        menu.addSeparator()

        act_edit = Action(FIF.EDIT, "Chỉnh sửa Profile", self)
        act_edit.triggered.connect(self._on_edit_account)
        menu.addAction(act_edit)

        act_folder = Action(FIF.FOLDER, "Mở thư mục Profile", self)
        act_folder.triggered.connect(self._on_open_profile_folder)
        menu.addAction(act_folder)

        menu.addSeparator()

        act_del = Action(FIF.DELETE, "Xóa Profile này", self)
        act_del.triggered.connect(self._on_delete_account)
        menu.addAction(act_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))
