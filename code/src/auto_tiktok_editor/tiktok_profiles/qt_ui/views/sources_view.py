"""SourcesView: Manage TikTok Source Channels per Profile with phone/browser launch."""

from __future__ import annotations

import sys
import webbrowser
from typing import Any

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
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
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
    ToolButton,
)
from qfluentwidgets.common.smooth_scroll import SmoothMode

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.phone_control import (
    PhoneController,
    load_phone_control_settings,
    normalize_phone_address,
)
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.instant_combo_box import InstantComboBox
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    ModernPhoneIcon,
    TIKTOK_ANDROID_PACKAGES,
    format_vietnam_datetime,
)

PHONE_ICON = ModernPhoneIcon()


class SourcesView(QWidget):
    """View to manage TikTok Source Channels for each Profile."""

    def __init__(
        self,
        manager: TikTokProfileManager,
        config: PipelineConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.config = config or PipelineConfig.from_env()
        self._sources_cache: list[Any] = []
        self._selected_source: Any | None = None
        self._accounts_map: dict[int, str] = {}
        self._profiles_signature: tuple[tuple[int, str], ...] | None = None

        self._init_ui()
        self.refresh_profiles_list()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Header Title
        self.title_label = SubtitleLabel("Quản lý Kênh Nguồn (Source Channels)", self)
        main_layout.addWidget(self.title_label)

        # Action Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.add_btn = PrimaryPushButton("Thêm Kênh Nguồn", self, FIF.ADD)
        self.add_btn.clicked.connect(self._on_new_source)
        toolbar.addWidget(self.add_btn)

        toolbar.addWidget(BodyLabel("Chọn Profile:", self))
        self.profile_combo = InstantComboBox(self)
        self.profile_combo.setFixedWidth(190)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_filter_changed)
        toolbar.addWidget(self.profile_combo)

        self.refresh_btn = PushButton("Làm mới", self, FIF.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_sources)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addStretch(1)

        main_layout.addLayout(toolbar)

        # Splitter: Table (Left) + Detail Card (Right)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)

        # Left: Table
        table_container = QWidget(self.splitter)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 8, 0)

        self.table = TableWidget(table_container)
        if hasattr(self.table, "scrollDelagate") and hasattr(self.table.scrollDelagate, "verticalSmoothScroll"):
            self.table.scrollDelagate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
            self.table.scrollDelagate.horizonSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Nổi bật",
            "Profile",
            "Tên Kênh",
            "TikTok URL / @Handle",
            "Ghi chú",
            "Cập nhật",
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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_source_selection_changed)

        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 110)

        table_layout.addWidget(self.table)
        self.splitter.addWidget(table_container)

        # Right: Detail Card
        detail_card = CardWidget(self.splitter)
        detail_card.setMinimumWidth(320)
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(18, 16, 18, 16)
        detail_layout.setSpacing(12)

        detail_layout.addWidget(SubtitleLabel("Chi tiết Kênh Nguồn", detail_card))

        detail_layout.addWidget(BodyLabel("Thuộc Profile:", detail_card))
        self.detail_account_combo = ComboBox(detail_card)
        detail_layout.addWidget(self.detail_account_combo)

        featured_row = QHBoxLayout()
        featured_row.addWidget(BodyLabel("Đánh dấu nổi bật (Ưu tiên):", detail_card))
        featured_row.addStretch(1)
        self.edit_featured_switch = SwitchButton(detail_card)
        self.edit_featured_switch.setOnText("Bật")
        self.edit_featured_switch.setOffText("Tắt")
        featured_row.addWidget(self.edit_featured_switch)
        detail_layout.addLayout(featured_row)

        detail_layout.addWidget(BodyLabel("Tên kênh:", detail_card))
        self.edit_name = LineEdit(detail_card)
        self.edit_name.setPlaceholderText("VD: Food Review, Kênh mẫu...")
        detail_layout.addWidget(self.edit_name)

        detail_layout.addWidget(BodyLabel("TikTok URL hoặc @handle:", detail_card))
        self.edit_url = LineEdit(detail_card)
        self.edit_url.setPlaceholderText("https://www.tiktok.com/@username")
        detail_layout.addWidget(self.edit_url)

        detail_layout.addWidget(BodyLabel("Ghi chú:", detail_card))
        self.edit_note = PlainTextEdit(detail_card)
        self.edit_note.setPlaceholderText("Ghi chú nội dung, phong cách kênh...")
        self.edit_note.setMaximumHeight(90)
        detail_layout.addWidget(self.edit_note)

        save_row = QHBoxLayout()
        self.save_btn = PrimaryPushButton("Lưu Kênh Nguồn", detail_card, FIF.SAVE)
        self.save_btn.clicked.connect(self._on_save_source)
        save_row.addWidget(self.save_btn)
        detail_layout.addLayout(save_row)

        detail_layout.addStretch(1)
        self.splitter.addWidget(detail_card)

        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        settings = QSettings("AutoTikTokEditor", "TikTokProfileManager")
        saved_state = settings.value("sources_view_splitter_state")
        if saved_state:
            self.splitter.restoreState(saved_state)
        else:
            self.splitter.setSizes([650, 350])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        main_layout.addWidget(self.splitter, 1)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        self._save_splitter_state()

    def _save_splitter_state(self) -> None:
        if hasattr(self, "splitter"):
            settings = QSettings("AutoTikTokEditor", "TikTokProfileManager")
            settings.setValue("sources_view_splitter_state", self.splitter.saveState())

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._save_splitter_state()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_profiles_list()

    def refresh_profiles_list(self) -> None:
        """Populate profile comboboxes."""
        try:
            current_selected_id = self.profile_combo.currentData()
            accounts = self.manager.list_accounts()
            signature = tuple((acc.id, acc.name) for acc in accounts)
            if signature == self._profiles_signature:
                return
            self._profiles_signature = signature
            self._accounts_map = {acc.id: acc.name for acc in accounts}

            self.profile_combo.blockSignals(True)
            self.detail_account_combo.blockSignals(True)

            self.profile_combo.clear()
            self.profile_combo.addItem("Tất cả Profile", userData=None)
            for acc in accounts:
                self.profile_combo.addItem(acc.name, userData=acc.id)

            self.detail_account_combo.clear()
            for acc in accounts:
                self.detail_account_combo.addItem(acc.name, userData=acc.id)

            # Restore selection if possible
            if current_selected_id is not None:
                for i in range(self.profile_combo.count()):
                    if self.profile_combo.itemData(i) == current_selected_id:
                        self.profile_combo.setCurrentIndex(i)
                        break

            self.profile_combo.blockSignals(False)
            self.detail_account_combo.blockSignals(False)
            self.refresh_sources()
        except Exception as exc:
            InfoBar.error("Lỗi tải Profiles", str(exc), parent=self.window())

    def set_active_profile(self, name: str | None) -> None:
        """Filter table to a specific profile name."""
        if not name or name == "Tất cả Profile":
            self.profile_combo.setCurrentIndex(0)
            return

        for i in range(self.profile_combo.count()):
            if self.profile_combo.itemText(i) == name:
                self.profile_combo.setCurrentIndex(i)
                return

    def apply_theme_mode(self, mode: str) -> None:
        """Refresh table for active theme."""
        self.refresh_sources()

    def refresh_sources(self) -> None:
        """Load sources from database and populate table."""
        try:
            selected_account_id = self.profile_combo.currentData()
            all_sources = self.manager.list_source_channels(selected_account_id)
            self._sources_cache = all_sources
            self._populate_table(all_sources)
        except Exception as exc:
            InfoBar.error("Lỗi tải Kênh Nguồn", str(exc), parent=self.window())

    def _populate_table(self, sources: list[Any]) -> None:
        from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode
        is_dark = get_current_theme_mode() == "dark"
        star_color = "#F2B84B" if is_dark else "#E89B20"

        self.table.blockSignals(True)
        self.table.setRowCount(len(sources))
        for row, s in enumerate(sources):
            # Col 0: Featured Star (Chỉ hiển thị icon ⭐ cho kênh nổi bật)
            featured_val = bool(getattr(s, "featured", 0))
            star_item = QTableWidgetItem("⭐" if featured_val else "")
            star_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if featured_val:
                star_item.setForeground(QColor(star_color))
                star_item.setToolTip("Kênh nổi bật (Ưu tiên lấy video)")
            star_item.setData(Qt.ItemDataRole.UserRole, s)

            # Col 1: Profile Name
            account_id = getattr(s, "account_id", None)
            profile_name = self._accounts_map.get(account_id, f"ID #{account_id}") if account_id else "—"
            profile_item = QTableWidgetItem(profile_name)

            # Col 2: Channel Name
            name_val = getattr(s, "name", "") or ""
            name_item = QTableWidgetItem(name_val)
            name_item.setData(Qt.ItemDataRole.UserRole, s)

            # Col 3: URL
            url_val = getattr(s, "url", "") or ""
            url_item = QTableWidgetItem(url_val)

            # Col 4: Note
            note_val = getattr(s, "note", "") or ""
            note_item = QTableWidgetItem(note_val)

            # Col 5: Updated At
            updated_val = getattr(s, "updated_at", "") or ""
            updated_item = QTableWidgetItem(format_vietnam_datetime(updated_val) if updated_val else "")

            self.table.setItem(row, 0, star_item)
            self.table.setItem(row, 1, profile_item)
            self.table.setItem(row, 2, name_item)
            self.table.setItem(row, 3, url_item)
            self.table.setItem(row, 4, note_item)
            self.table.setItem(row, 5, updated_item)

            # Col 6: Actions
            action_widget = QWidget(self.table)
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            # 1. Open on Phone
            btn_phone = ToolButton(action_widget)
            btn_phone.setIcon(PHONE_ICON)
            btn_phone.setFixedHeight(28)
            btn_phone.setToolTip("Mở kênh này trên điện thoại (ADB)")
            btn_phone.clicked.connect(lambda _, src=s: self._open_on_phone(src))
            action_layout.addWidget(btn_phone)

            # 2. Open on Web
            btn_web = ToolButton(action_widget)
            btn_web.setIcon(FIF.GLOBE)
            btn_web.setFixedHeight(28)
            btn_web.setToolTip("Mở kênh trên trình duyệt web")
            btn_web.clicked.connect(lambda _, src=s: self._open_on_web(src))
            action_layout.addWidget(btn_web)

            # 3. Delete
            btn_del = ToolButton(action_widget)
            btn_del.setIcon(FIF.DELETE)
            btn_del.setFixedHeight(28)
            btn_del.setToolTip("Xóa kênh này")
            btn_del.clicked.connect(lambda _, src=s: self._on_delete_source(src))
            action_layout.addWidget(btn_del)

            self.table.setCellWidget(row, 6, action_widget)

        self.table.blockSignals(False)

    def _on_profile_filter_changed(self) -> None:
        self.refresh_sources()
        # Auto select the same profile in detail combo if a specific profile is selected
        selected_acc_id = self.profile_combo.currentData()
        if selected_acc_id is not None:
            for i in range(self.detail_account_combo.count()):
                if self.detail_account_combo.itemData(i) == selected_acc_id:
                    self.detail_account_combo.setCurrentIndex(i)
                    break

    def _on_source_selection_changed(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self._selected_source = None
            return

        row = selected_rows[0].row()
        item = self.table.item(row, 0) or self.table.item(row, 2)
        if item:
            source = item.data(Qt.ItemDataRole.UserRole)
            self._selected_source = source
            if source:
                # Find profile in detail combo
                for i in range(self.detail_account_combo.count()):
                    if self.detail_account_combo.itemData(i) == source.account_id:
                        self.detail_account_combo.setCurrentIndex(i)
                        break

                self.edit_featured_switch.setChecked(bool(getattr(source, "featured", 0)))
                self.edit_name.setText(getattr(source, "name", "") or "")
                self.edit_url.setText(getattr(source, "url", "") or "")
                self.edit_note.setPlainText(getattr(source, "note", "") or "")

    def _on_new_source(self) -> None:
        self.table.clearSelection()
        self._selected_source = None
        self.edit_featured_switch.setChecked(False)
        self.edit_name.clear()
        self.edit_url.clear()
        self.edit_note.clear()
        self.edit_url.setFocus()
        InfoBar.info("Thêm kênh nguồn", "Điền thông tin ở bảng bên phải rồi bấm 'Lưu Kênh Nguồn'", parent=self.window())

    def _on_save_source(self) -> None:
        account_id = self.detail_account_combo.currentData()
        if account_id is None:
            InfoBar.warning("Chưa chọn Profile", "Vui lòng chọn Profile tương ứng cho kênh nguồn!", parent=self.window())
            return

        url = self.edit_url.text().strip()
        if not url:
            InfoBar.warning("Thiếu URL", "Vui lòng nhập TikTok URL hoặc @handle!", parent=self.window())
            return

        name = self.edit_name.text().strip()
        note = self.edit_note.toPlainText().strip()
        featured = self.edit_featured_switch.isChecked()

        try:
            if self._selected_source:
                # Update
                self.manager.update_source_channel(
                    channel_id=self._selected_source.id,
                    account_id=account_id,
                    name=name,
                    url=url,
                    note=note,
                    featured=featured,
                    enabled=True,
                )
                InfoBar.success("Đã cập nhật", f"Đã cập nhật kênh nguồn '{name or url}' thành công!", parent=self.window())
            else:
                # Add
                self.manager.add_source_channel(
                    account_id=account_id,
                    name=name,
                    url=url,
                    note=note,
                    featured=featured,
                    enabled=True,
                )
                InfoBar.success("Đã thêm", f"Đã thêm kênh nguồn '{name or url}' thành công!", parent=self.window())

            self.refresh_sources()
        except Exception as exc:
            InfoBar.error("Lỗi lưu Kênh Nguồn", str(exc), parent=self.window())

    def _toggle_featured(self, source: Any) -> None:
        if not source:
            return
        try:
            new_state = not bool(getattr(source, "featured", 0))
            self.manager.set_source_channel_featured(source.id, new_state)
            self.refresh_sources()
        except Exception as exc:
            InfoBar.error("Lỗi cập nhật Nổi bật", str(exc), parent=self.window())

    def _on_open_selected_on_phone(self) -> None:
        if not self._selected_source:
            InfoBar.warning("Chưa chọn kênh", "Vui lòng chọn kênh nguồn cần mở trên điện thoại!", parent=self.window())
            return
        self._open_on_phone(self._selected_source)

    def _adb_device_serials(self, controller: PhoneController) -> list[str]:
        completed = controller.runner.run([self.config.adb_bin, "devices"], check=False)
        serials = []
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            if "\tdevice" in line:
                serials.append(line.split("\t", 1)[0].strip())
        return serials

    def _resolve_source_phone_target(self, controller: PhoneController) -> str:
        phone_settings = load_phone_control_settings()
        address = str(getattr(phone_settings, "address", "") or "").strip()
        device_serials = self._adb_device_serials(controller)
        if address:
            candidates = [address]
            try:
                candidates.append(normalize_phone_address(address))
            except Exception:
                pass
            host = address.rsplit(":", 1)[0] if ":" in address else address
            for candidate in candidates:
                if candidate in device_serials:
                    return candidate
            host_matches = [serial for serial in device_serials if serial == host or serial.startswith(f"{host}:")]
            if len(host_matches) == 1:
                return host_matches[0]
            if len(device_serials) == 1:
                return device_serials[0]
            if not device_serials:
                raise RuntimeError("Chưa có thiết bị ADB online. Vui lòng kết nối trong tab 'Điện thoại (ADB)' trước.")
            raise RuntimeError(f"Địa chỉ '{address}' không khớp thiết bị nào đang online.")
        if len(device_serials) == 1:
            return device_serials[0]
        if not device_serials:
            raise RuntimeError("Chưa có thiết bị ADB nào kết nối. Vui lòng cắm cáp hoặc kết nối WiFi trong tab 'Điện thoại (ADB)'.")
        raise RuntimeError("Có nhiều thiết bị kết nối. Vui lòng chọn địa chỉ điện thoại cụ thể trong tab 'Điện thoại (ADB)'.")

    def _installed_tiktok_package(self, controller: PhoneController, target: str) -> str:
        for package_name in TIKTOK_ANDROID_PACKAGES:
            completed = controller.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "pm",
                    "path",
                    package_name,
                ],
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return package_name
        return ""

    def _open_on_phone(self, source: Any) -> None:
        raw_url = getattr(source, "url", "") or ""
        raw_url = raw_url.strip()
        if not raw_url:
            InfoBar.warning("Thiếu liên kết", "Kênh nguồn này chưa có URL/handle TikTok!", parent=self.window())
            return

        # Chuẩn hóa URL sang định dạng TikTok hợp lệ
        if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
            clean_handle = raw_url if raw_url.startswith("@") else f"@{raw_url}"
            target_url = f"https://www.tiktok.com/{clean_handle}"
        else:
            target_url = raw_url

        try:
            controller = PhoneController(self.config)
            controller.runner.ensure_tool(self.config.adb_bin)
            target = self._resolve_source_phone_target(controller)
            package_name = self._installed_tiktok_package(controller, target)

            opened = False
            if package_name:
                forced = controller.runner.run(
                    [
                        self.config.adb_bin,
                        "-s",
                        target,
                        "shell",
                        "am",
                        "start",
                        "-a",
                        "android.intent.action.VIEW",
                        "-d",
                        target_url,
                        "-p",
                        package_name,
                    ],
                    check=False,
                )
                if forced.returncode == 0:
                    opened = True

            if not opened:
                fallback = controller.runner.run(
                    [
                        self.config.adb_bin,
                        "-s",
                        target,
                        "shell",
                        "am",
                        "start",
                        "-a",
                        "android.intent.action.VIEW",
                        "-d",
                        target_url,
                    ],
                    check=False,
                )
                if fallback.returncode == 0:
                    opened = True
                else:
                    detail = (fallback.stderr or "").strip()
                    raise RuntimeError(detail or "Không thể mở liên kết trên điện thoại.")

            # Ghi log sự kiện backend
            try:
                self.manager.add_log(
                    "info",
                    "source_open_phone",
                    f"Opened source channel on phone: {target_url}",
                    account_id=getattr(source, "account_id", None),
                )
            except Exception:
                pass

            InfoBar.success(
                title="Đã mở trên ĐT",
                content=f"Đã mở kênh '{getattr(source, 'name', '') or raw_url}' trên ứng dụng TikTok ({target})!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
        except Exception as exc:
            InfoBar.error(
                title="Lỗi mở trên điện thoại",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _on_open_selected_on_web(self) -> None:
        if not self._selected_source:
            InfoBar.warning("Chưa chọn kênh", "Vui lòng chọn kênh nguồn cần mở trên web!", parent=self.window())
            return
        self._open_on_web(self._selected_source)

    def _open_on_web(self, source: Any) -> None:
        url = getattr(source, "url", "") or ""
        if not url:
            return
        try:
            if not (url.startswith("http://") or url.startswith("https://")):
                url = f"https://www.tiktok.com/{url if url.startswith('@') else '@' + url}"
            webbrowser.open(url)
            InfoBar.info("Đang mở trình duyệt", f"Đang mở URL: {url}", parent=self.window())
        except Exception as exc:
            InfoBar.error("Lỗi mở web", str(exc), parent=self.window())

    def _on_delete_source(self, single_source: Any | None = None) -> None:
        source = single_source or self._selected_source
        if not source:
            InfoBar.warning("Chưa chọn kênh", "Vui lòng chọn kênh nguồn cần xóa!", parent=self.window())
            return

        box = MessageBox(
            title="Xác nhận xóa Kênh Nguồn",
            content=f"Bạn có chắc muốn xóa kênh '{getattr(source, 'name', '') or getattr(source, 'url', '')}' khỏi danh sách?",
            parent=self.window(),
        )
        if box.exec():
            try:
                self.manager.delete_source_channel(source.id)
                InfoBar.success("Đã xóa", "Đã xóa kênh nguồn thành công.", parent=self.window())
                self._selected_source = None
                self._on_new_source()
                self.refresh_sources()
            except Exception as exc:
                InfoBar.error("Lỗi xóa Kênh Nguồn", str(exc), parent=self.window())

    def _show_context_menu(self, pos: QPoint) -> None:
        if not self._selected_source:
            return

        menu = RoundMenu(parent=self)
        is_featured = bool(getattr(self._selected_source, "featured", 0))

        act_star = Action(FIF.ASTERISK, "Bỏ nổi bật" if is_featured else "⭐ Đánh dấu Nổi bật", self)
        act_star.triggered.connect(lambda: self._toggle_featured(self._selected_source))
        menu.addAction(act_star)

        act_phone = Action(ModernPhoneIcon(), "📱 Mở trên điện thoại (ADB)", self)
        act_phone.triggered.connect(lambda: self._open_on_phone(self._selected_source))
        menu.addAction(act_phone)

        act_web = Action(FIF.GLOBE, "🌐 Mở trên trình duyệt web", self)
        act_web.triggered.connect(lambda: self._open_on_web(self._selected_source))
        menu.addAction(act_web)

        menu.addSeparator()

        act_del = Action(FIF.DELETE, "🗑 Xóa kênh này", self)
        act_del.triggered.connect(lambda: self._on_delete_source(self._selected_source))
        menu.addAction(act_del)

        menu.exec(self.table.viewport().mapToGlobal(pos))
