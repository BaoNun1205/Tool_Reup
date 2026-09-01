"""Fashion prompt library and direct video transfer workspace."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QScrollArea,
    QTabWidget,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)

from auto_tiktok_editor.app.fashion_products import generate_fashion_product_description
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.fashion_prompt_settings import (
    add_garment_preset,
    load_change_outfit_prompt,
    load_garment_presets,
    load_garment_prompts,
    save_change_outfit_prompt,
    save_garment_prompts,
)
from auto_tiktok_editor.phone_control import PhoneController, load_phone_control_settings
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import (
    DARK_BORDER,
    DARK_CARD_HOVER,
    DARK_TEXT_MAIN,
    DARK_TEXT_SECONDARY,
    LIGHT_BORDER,
    LIGHT_CARD,
    LIGHT_CARD_HOVER,
    LIGHT_TEXT_MAIN,
    LIGHT_TEXT_SECONDARY,
    get_current_theme_mode,
    format_fashion_product_status,
    format_vietnam_datetime,
)
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import WorkerThread


_SETTINGS_ORGANIZATION = "AutoTikTokEditor"
_SETTINGS_APPLICATION = "TikTokProfileManager"
_VIDEO_DIRECTORY_KEY = "fashion/video_directory"


def _existing_video_directory(value: object) -> Path | None:
    directory_text = str(value or "").strip().strip('"')
    if not directory_text:
        return None
    try:
        directory = Path(directory_text).expanduser()
        return directory.resolve() if directory.is_dir() else None
    except (OSError, RuntimeError):
        return None


class PromptEditorDialog(MessageBoxBase):
    """Modal editor for one compact garment prompt card."""

    def __init__(
        self,
        parent: QWidget,
        name: str = "",
        content: str = "",
        *,
        content_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.result_data: tuple[str, str] | None = None

        self.titleLabel = SubtitleLabel("Chỉnh sửa prompt", self)
        self.viewLayout.addWidget(self.titleLabel)

        form = QFormLayout()
        form.setContentsMargins(0, 16, 0, 16)
        form.setSpacing(12)
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("Tên prompt")
        self.name_edit.setText(name)
        if not content_only:
            form.addRow(BodyLabel("Tên:", self), self.name_edit)

        self.content_edit = PlainTextEdit(self)
        self.content_edit.setPlaceholderText("Nhập nội dung prompt...")
        self.content_edit.setPlainText(content)
        self.content_edit.setMinimumHeight(220)
        form.addRow(BodyLabel("Nội dung:", self), self.content_edit)
        self.viewLayout.addLayout(form)

        self.yesButton.setText("Lưu")
        self.cancelButton.setText("Hủy")
        self.widget.setMinimumWidth(560)

    def validate(self) -> bool:
        name = self.name_edit.text().strip()
        content = self.content_edit.toPlainText().strip()
        if not name or not content:
            InfoBar.warning(
                title="Prompt chưa hoàn chỉnh",
                content="Mỗi prompt cần có cả tên và nội dung.",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return False
        self.result_data = (name, content)
        return True


class FashionProductDetailsDialog(MessageBoxBase):
    """Preview the full Fashion product information without editing its row."""

    def __init__(self, parent: QWidget, manager: TikTokProfileManager, product: Any) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("Chi tiết sản phẩm Fashion", self)
        self.viewLayout.addWidget(self.titleLabel)

        image_label = QLabel(self)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumSize(440, 300)
        image_label.setStyleSheet("border: 1px solid #555B69; border-radius: 8px;")
        image_path = manager.resolve_fashion_product_image_path(product)
        pixmap = QPixmap(str(image_path)) if image_path and image_path.is_file() else QPixmap()
        if pixmap.isNull():
            image_label.setText("Không tìm thấy ảnh sản phẩm")
        else:
            image_label.setPixmap(
                pixmap.scaled(
                    520,
                    420,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.viewLayout.addWidget(image_label)

        form = QFormLayout()
        form.setContentsMargins(0, 16, 0, 8)
        form.setSpacing(10)
        name_label = BodyLabel(product.product_name, self)
        name_label.setWordWrap(True)
        description_label = BodyLabel(product.description, self)
        description_label.setWordWrap(True)
        product_id_label = BodyLabel(product.product_id or "-", self)
        form.addRow(BodyLabel("Tên sản phẩm:", self), name_label)
        form.addRow(BodyLabel("Mô tả:", self), description_label)
        form.addRow(BodyLabel("Product ID:", self), product_id_label)
        self.viewLayout.addLayout(form)

        self.yesButton.setText("Đóng")
        self.cancelButton.hide()
        self.widget.setMinimumWidth(600)


class FashionView(QWidget):
    """Manage garment prompts and send completed videos to a phone."""

    def __init__(
        self,
        manager: TikTokProfileManager,
        config: PipelineConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.config = config or PipelineConfig.from_env()
        self._video_transfer_worker: WorkerThread | None = None
        self._video_file_path: Path | None = None
        self._fashion_job_workers: list[WorkerThread] = []
        self._fashion_job_ids_in_progress: set[int] = set()
        self._fashion_products_signature: tuple[object, ...] | None = None
        self._garment_prompt_widgets: list[tuple[CardWidget, str, str]] = []
        self._garment_presets = load_garment_presets()
        self._init_ui()
        self.apply_theme_mode(get_current_theme_mode())

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.fashion_tabs = QTabWidget(self)
        self.fashion_tabs.setObjectName("fashionTabs")
        self.fashion_tabs.tabBar().setExpanding(False)
        self.fashion_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        products_tab = QWidget(self.fashion_tabs)
        products_tab.setObjectName("fashionProductsTab")
        products_tab_layout = QVBoxLayout(products_tab)
        products_tab_layout.setContentsMargins(24, 20, 24, 20)
        products_tab_layout.setSpacing(16)

        products_tab_layout.addWidget(SubtitleLabel("Fashion", products_tab))
        description = BodyLabel(
            "Sản phẩm Fashion được bot nhận từ link TikTok Shop và tự tạo mô tả bằng Gemini.",
            products_tab,
        )
        description.setWordWrap(True)
        products_tab_layout.addWidget(description)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("fashionScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(self.scroll_area)
        content.setObjectName("fashionScrollContent")
        self.scroll_area.viewport().setObjectName("fashionScrollViewport")
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        products_card = CardWidget(products_tab)
        products_card.setObjectName("fashionProductsCard")
        products_layout = QVBoxLayout(products_card)
        products_layout.setContentsMargins(20, 18, 20, 18)
        products_layout.setSpacing(10)
        products_layout.addWidget(SubtitleLabel("Sản phẩm Fashion", products_card))
        products_desc = BodyLabel(
            "Mỗi link bot nhận được tạo một dòng ở đây. Nút gửi sẽ sao chép Mô tả và Product ID vào bộ nhớ tạm; không cần có video Fashion.",
            products_card,
        )
        products_desc.setWordWrap(True)
        products_layout.addWidget(products_desc)
        self.products_table = TableWidget(products_card)
        self.products_table.setColumnCount(7)
        self.products_table.setHorizontalHeaderLabels([
            "Image",
            "Ngày tạo",
            "Tên sản phẩm",
            "Mô tả",
            "Link sản phẩm",
            "Trạng thái",
            "Hành động",
        ])
        self.products_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.products_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.products_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.products_table.setColumnWidth(0, 100)
        self.products_table.setColumnWidth(1, 150)
        self.products_table.setColumnWidth(4, 240)
        self.products_table.setColumnWidth(5, 120)
        self.products_table.setColumnWidth(6, 140)
        self.products_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.products_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.products_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.products_table.setAlternatingRowColors(True)
        self.products_table.setShowGrid(False)
        self.products_table.verticalHeader().setDefaultSectionSize(82)
        self.products_table.cellClicked.connect(self._on_fashion_product_cell_clicked)
        self.products_table.cellDoubleClicked.connect(self._on_fashion_product_double_clicked)
        products_layout.addWidget(self.products_table, 1)
        products_tab_layout.addWidget(products_card, 1)

        preset_card = CardWidget(self)
        preset_layout = QVBoxLayout(preset_card)
        preset_layout.setContentsMargins(20, 18, 20, 18)
        preset_layout.setSpacing(12)
        preset_layout.addWidget(SubtitleLabel("Prompt theo loại áo", preset_card))
        preset_desc = BodyLabel(
            "Chọn loại áo để xem và chỉnh sửa danh sách prompt phù hợp.",
            preset_card,
        )
        preset_desc.setWordWrap(True)
        preset_layout.addWidget(preset_desc)

        preset_layout.addWidget(BodyLabel("Promt thay đồ", preset_card))
        self._change_outfit_prompt = load_change_outfit_prompt()
        self.change_outfit_card = CardWidget(preset_card)
        self.change_outfit_card.setObjectName("fashionPromptCard")
        change_outfit_layout = QHBoxLayout(self.change_outfit_card)
        change_outfit_layout.setContentsMargins(14, 10, 14, 10)
        change_outfit_layout.setSpacing(8)

        self.change_outfit_preview = BodyLabel("", self.change_outfit_card)
        self.change_outfit_preview.setObjectName("fashionPromptPreview")
        self.change_outfit_preview.setWordWrap(False)
        change_outfit_layout.addWidget(self.change_outfit_preview, 1)

        copy_change_outfit_button = self._prompt_action_button(
            self.change_outfit_card,
            FIF.COPY,
            "Sao chép prompt",
        )
        copy_change_outfit_button.clicked.connect(
            lambda: self._copy_prompt_text(self._change_outfit_prompt[1])
        )
        change_outfit_layout.addWidget(copy_change_outfit_button)
        edit_change_outfit_button = self._prompt_action_button(
            self.change_outfit_card,
            FIF.EDIT,
            "Chỉnh sửa prompt chung",
        )
        edit_change_outfit_button.clicked.connect(self._edit_change_outfit_prompt)
        change_outfit_layout.addWidget(edit_change_outfit_button)
        self.change_outfit_card.setFixedHeight(58)
        self._refresh_change_outfit_prompt_card()
        self._apply_prompt_card_theme(self.change_outfit_card, get_current_theme_mode())
        preset_layout.addWidget(self.change_outfit_card)

        garment_row = QHBoxLayout()
        garment_row.addWidget(BodyLabel("Loại áo:", preset_card))
        self.garment_combo = ComboBox(preset_card)
        for preset in self._garment_presets:
            self.garment_combo.addItem(preset.label, userData=preset.key)
        self.garment_combo.currentIndexChanged.connect(self._on_garment_changed)
        self.garment_combo.setFixedWidth(220)
        garment_row.addWidget(self.garment_combo)
        self.add_garment_button = PushButton("Thêm loại", preset_card, FIF.ADD)
        self.add_garment_button.clicked.connect(self._add_garment_type)
        garment_row.addWidget(self.add_garment_button)
        garment_row.addStretch(1)
        preset_layout.addLayout(garment_row)

        prompt_list_header = QHBoxLayout()
        prompt_list_header.addWidget(BodyLabel("Danh sách prompt", preset_card))
        prompt_list_header.addStretch(1)
        self.add_prompt_button = PrimaryPushButton("Thêm prompt", preset_card, FIF.ADD)
        self.add_prompt_button.clicked.connect(self._on_add_garment_prompt)
        prompt_list_header.addWidget(self.add_prompt_button)
        preset_layout.addLayout(prompt_list_header)
        self.garment_prompts_container = QWidget(preset_card)
        self.garment_prompts_layout = QGridLayout(self.garment_prompts_container)
        self.garment_prompts_layout.setContentsMargins(0, 0, 0, 0)
        self.garment_prompts_layout.setSpacing(10)
        for column in range(4):
            self.garment_prompts_layout.setColumnStretch(column, 1)
        preset_layout.addWidget(self.garment_prompts_container)
        preset_actions = QHBoxLayout()
        self.save_preset_button = PrimaryPushButton("Lưu chỉnh sửa", preset_card, FIF.SAVE)
        self.save_preset_button.clicked.connect(self._save_preset_prompts)
        preset_actions.addWidget(self.save_preset_button)
        preset_actions.addStretch(1)
        preset_layout.addLayout(preset_actions)
        self._on_garment_changed(self.garment_combo.currentIndex())
        main_layout.addWidget(preset_card)

        transfer_card = CardWidget(self)
        transfer_layout = QVBoxLayout(transfer_card)
        transfer_layout.setContentsMargins(18, 14, 18, 14)
        transfer_layout.setSpacing(8)
        transfer_layout.addWidget(SubtitleLabel("Gửi video sang điện thoại", transfer_card))
        transfer_desc = BodyLabel(
            "Chọn nhanh video từ thư mục mặc định và gửi vào Gallery qua kết nối ADB.",
            transfer_card,
        )
        transfer_desc.setWordWrap(True)
        transfer_layout.addWidget(transfer_desc)

        video_picker_row = QHBoxLayout()
        video_picker_row.setSpacing(8)
        video_picker_row.addWidget(BodyLabel("Thư mục:", transfer_card))
        self.video_directory_edit = LineEdit(transfer_card)
        self.video_directory_edit.setPlaceholderText("Thư mục video mặc định...")
        self.video_directory_edit.setText(self._load_video_directory())
        self.video_directory_edit.editingFinished.connect(self._save_video_directory)
        video_picker_row.addWidget(self.video_directory_edit, 1)
        self.choose_video_directory_button = PushButton(
            "Chọn thư mục",
            transfer_card,
            FIF.FOLDER,
        )
        self.choose_video_directory_button.clicked.connect(self._choose_video_directory)
        video_picker_row.addWidget(self.choose_video_directory_button)
        self.choose_video_button = PrimaryPushButton("Chọn video", transfer_card, FIF.VIDEO)
        self.choose_video_button.clicked.connect(self._choose_video_file)
        video_picker_row.addWidget(self.choose_video_button)
        self.send_video_button = PushButton("Gửi sang điện thoại", transfer_card, FIF.SEND)
        self.send_video_button.setEnabled(False)
        self.send_video_button.clicked.connect(self._send_video_to_phone)
        video_picker_row.addWidget(self.send_video_button)
        transfer_layout.addLayout(video_picker_row)

        self.video_file_label = BodyLabel("Chưa chọn video", transfer_card)
        self.video_file_label.setWordWrap(True)
        transfer_layout.addWidget(self.video_file_label)
        main_layout.addWidget(transfer_card)

        main_layout.addStretch(1)

        self.scroll_area.setWidget(content)
        self.fashion_tabs.addTab(products_tab, "Sản phẩm")
        self.fashion_tabs.setTabToolTip(0, "Danh sách sản phẩm và mô tả do bot Fashion tạo")
        self.fashion_tabs.addTab(self.scroll_area, "Prompt video")
        self.fashion_tabs.setTabToolTip(1, "Quản lý prompt Fashion và chuyển video sang điện thoại")
        outer_layout.addWidget(self.fashion_tabs)
        self._fashion_sync_timer = QTimer(self)
        self._fashion_sync_timer.timeout.connect(self.refresh_fashion_products)
        self._fashion_sync_timer.start(3000)
        self.refresh_fashion_products()

    def refresh_fashion_products(self, force: bool = False) -> None:
        try:
            products = self.manager.list_fashion_products()
        except Exception:
            return
        signature = tuple(
            (
                product.id,
                product.product_name,
                product.description,
                product.product_id,
                product.status,
                product.video_path,
                product.updated_at,
            )
            for product in products
        )
        if not force and signature == self._fashion_products_signature:
            return
        self._fashion_products_signature = signature

        reusable_image_widgets: dict[int, tuple[object, QWidget]] = {}
        for row in range(self.products_table.rowCount()):
            existing_item = self.products_table.item(row, 1)
            existing_product = (
                existing_item.data(Qt.ItemDataRole.UserRole)
                if existing_item is not None
                else None
            )
            existing_widget = self.products_table.cellWidget(row, 0)
            if existing_product is not None and existing_widget is not None:
                reusable_image_widgets[row] = (existing_product, existing_widget)

        self.products_table.setRowCount(len(products))
        for row, product in enumerate(products):
            created_item = QTableWidgetItem(format_vietnam_datetime(product.created_at))
            created_item.setData(Qt.ItemDataRole.UserRole, product)
            name_item = QTableWidgetItem(product.product_name)
            name_item.setToolTip(product.product_name)
            description_item = QTableWidgetItem(product.description)
            description_item.setToolTip(product.description)
            product_link_item = QTableWidgetItem(product.product_url)
            product_link_item.setToolTip("Mở link sản phẩm")
            product_link_item.setForeground(QColor("#3B82F6"))
            product_link_font = product_link_item.font()
            product_link_font.setUnderline(True)
            product_link_item.setFont(product_link_font)
            status_text, status_color = format_fashion_product_status(product.status)
            status_item = QTableWidgetItem(status_text)
            status_font = status_item.font()
            status_font.setBold(True)
            status_item.setFont(status_font)
            status_item.setForeground(QColor(status_color))
            reusable_image = reusable_image_widgets.get(row)
            if not self._can_reuse_fashion_image_widget(reusable_image, product):
                self.products_table.setCellWidget(row, 0, self._fashion_image_widget(product))
            self.products_table.setItem(row, 1, created_item)
            self.products_table.setItem(row, 2, name_item)
            self.products_table.setItem(row, 3, description_item)
            self.products_table.setItem(row, 4, product_link_item)
            self.products_table.setItem(row, 5, status_item)
            self.products_table.setCellWidget(row, 6, self._fashion_action_widget(product))

    @staticmethod
    def _can_reuse_fashion_image_widget(
        reusable_image: tuple[object, QWidget] | None,
        product: Any,
    ) -> bool:
        if reusable_image is None:
            return False
        existing_product, _widget = reusable_image
        return (
            getattr(existing_product, "id", None) == getattr(product, "id", None)
            and str(getattr(existing_product, "image_path", "") or "")
            == str(getattr(product, "image_path", "") or "")
        )

    def _fashion_image_widget(self, product: Any) -> QWidget:
        container = QWidget(self.products_table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label = QLabel(container)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setFixedSize(68, 68)
        image_label.setStyleSheet("border: 1px solid #555B69; border-radius: 6px;")
        image_path = self.manager.resolve_fashion_product_image_path(product)
        pixmap = QPixmap(str(image_path)) if image_path and image_path.is_file() else QPixmap()
        if pixmap.isNull():
            image_label.setText("—")
        else:
            image_label.setPixmap(
                pixmap.scaled(
                    62,
                    62,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(image_label)
        return container

    def _fashion_action_widget(self, product: Any) -> QWidget:
        action_widget = QWidget(self.products_table)
        # TableWidget's delegate positions index widgets by their current height,
        # then tries to stretch them to the whole row. Keeping this wrapper at the
        # exact action-row height makes it stay vertically centred after refreshes.
        action_widget.setFixedHeight(32)
        container_layout = QVBoxLayout(action_widget)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(0)
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        action_layout.addStretch(1)
        is_working = product.id in self._fashion_job_ids_in_progress or product.status == "processing"

        view_button = ToolButton(action_widget)
        view_button.setIcon(FIF.VIEW)
        view_button.setFixedSize(28, 28)
        view_button.setToolTip("Xem chi tiết sản phẩm")
        view_button.clicked.connect(lambda _checked=False, item=product: self._show_fashion_product_details(item))
        action_layout.addWidget(view_button)

        regenerate_button = ToolButton(action_widget)
        regenerate_button.setIcon(FIF.SYNC)
        regenerate_button.setFixedSize(28, 28)
        regenerate_button.setEnabled(not is_working)
        regenerate_button.setToolTip("Tạo lại mô tả bằng Gemini")
        regenerate_button.clicked.connect(lambda _checked=False, item=product: self._regenerate_fashion_product(item))
        action_layout.addWidget(regenerate_button)

        send_button = ToolButton(action_widget)
        send_button.setIcon(FIF.SEND)
        send_button.setFixedSize(28, 28)
        can_send = not is_working and product.status in {"ready", "sent"}
        send_button.setEnabled(can_send)
        send_button.setToolTip(
            "Sao chép Mô tả và Product ID vào bộ nhớ tạm"
            if can_send
            else "Sản phẩm đang được tạo mô tả, chưa thể sao chép"
        )
        send_button.clicked.connect(lambda _checked=False, item=product: self._copy_fashion_product_to_clipboard(item))
        action_layout.addWidget(send_button)

        delete_button = ToolButton(action_widget)
        delete_button.setIcon(FIF.DELETE)
        delete_button.setFixedSize(28, 28)
        delete_button.setEnabled(not is_working)
        delete_button.setToolTip("Xóa sản phẩm Fashion")
        delete_button.clicked.connect(lambda _checked=False, item=product: self._delete_fashion_product(item))
        action_layout.addWidget(delete_button)
        action_layout.addStretch(1)
        container_layout.addStretch(1)
        container_layout.addLayout(action_layout)
        container_layout.addStretch(1)
        return action_widget

    def _regenerate_fashion_product(self, product: Any) -> None:
        if product.id in self._fashion_job_ids_in_progress:
            return
        self._fashion_job_ids_in_progress.add(product.id)
        self.refresh_fashion_products(force=True)

        def _generate() -> Any:
            fresh = self.manager.get_fashion_product(product.id)
            if fresh is None:
                raise ValueError("Không tìm thấy sản phẩm Fashion.")
            return generate_fashion_product_description(self.manager, fresh)

        thread = WorkerThread(_generate, parent=self)
        self._fashion_job_workers.append(thread)

        def _finish() -> None:
            self._fashion_job_ids_in_progress.discard(product.id)
            if thread in self._fashion_job_workers:
                self._fashion_job_workers.remove(thread)
            self.refresh_fashion_products(force=True)

        def _done(_result: Any) -> None:
            _finish()
            InfoBar.success("Đã tạo lại", "Đã cập nhật Mô tả Fashion bằng Gemini.", parent=self.window())

        def _error(error: Exception, _traceback: str) -> None:
            try:
                self.manager.update_fashion_product_status(product.id, "error", note=str(error))
            except Exception:
                pass
            _finish()
            InfoBar.error("Không thể tạo lại mô tả", str(error), parent=self.window())

        thread.finished_task.connect(_done)
        thread.error_task.connect(_error)
        thread.start()

    def _copy_fashion_product_to_clipboard(self, product: Any) -> None:
        """Copy the product's publish data without requiring a Fashion video."""
        if product.id in self._fashion_job_ids_in_progress:
            return
        description = str(product.description or "").strip()
        product_id = str(product.product_id or "").strip()
        if not description and not product_id:
            InfoBar.warning(
                "Chưa có nội dung",
                "Sản phẩm này chưa có Mô tả hoặc Product ID để sao chép.",
                parent=self.window(),
            )
            return

        self._fashion_job_ids_in_progress.add(product.id)
        self.refresh_fashion_products(force=True)

        def _copy() -> dict[str, object]:
            phone_settings = load_phone_control_settings()
            address = str(getattr(phone_settings, "address", "") or "").strip()
            sync_to_phone = bool(address)
            controller = PhoneController(self.config)
            if description:
                controller.copy_text_to_clipboard(
                    description,
                    label="Fashion description",
                    address=address,
                    sync_to_phone=sync_to_phone,
                    require_phone_clipboard=False,
                )
            if product_id:
                controller.copy_text_to_clipboard(
                    product_id,
                    label="Product ID",
                    address=address,
                    sync_to_phone=sync_to_phone,
                    require_phone_clipboard=False,
                )
            self.manager.update_fashion_product_status(product.id, "sent")
            self.manager.add_log(
                "info",
                "fashion_product_clipboard",
                "Đã sao chép Mô tả + Product ID cho sản phẩm Fashion '%s'." % product.product_name,
            )
            return {"synced_to_phone": sync_to_phone}

        thread = WorkerThread(_copy, parent=self)
        self._fashion_job_workers.append(thread)

        def _finish() -> None:
            self._fashion_job_ids_in_progress.discard(product.id)
            if thread in self._fashion_job_workers:
                self._fashion_job_workers.remove(thread)
            self.refresh_fashion_products(force=True)

        def _done(result: Any) -> None:
            _finish()
            target = "điện thoại và máy tính" if result.get("synced_to_phone") else "máy tính"
            InfoBar.success(
                "Đã sao chép",
                "Mô tả và Product ID đã được đưa vào bộ nhớ tạm %s." % target,
                parent=self.window(),
            )

        def _error(error: Exception, _traceback: str) -> None:
            _finish()
            InfoBar.error("Không thể sao chép sản phẩm Fashion", str(error), parent=self.window())

        thread.finished_task.connect(_done)
        thread.error_task.connect(_error)
        thread.start()

    def _delete_fashion_product(self, product: Any) -> None:
        box = MessageBox(
            title="Xác nhận xóa sản phẩm Fashion",
            content="Bạn có chắc muốn xóa '%s' khỏi bảng Fashion?" % product.product_name,
            parent=self.window(),
        )
        if not box.exec():
            return
        try:
            self.manager.delete_fashion_products([product.id])
        except Exception as exc:
            InfoBar.error("Không thể xóa sản phẩm Fashion", str(exc), parent=self.window())
            return
        self.refresh_fashion_products(force=True)
        InfoBar.success("Đã xóa", "Đã xóa sản phẩm Fashion.", parent=self.window())

    def _on_fashion_product_double_clicked(self, row: int, _column: int) -> None:
        product = self._fashion_product_for_row(row)
        if product is not None:
            self._show_fashion_product_details(product)

    def _on_fashion_product_cell_clicked(self, row: int, column: int) -> None:
        if column != 4:
            return
        product = self._fashion_product_for_row(row)
        product_url = str(getattr(product, "product_url", "") or "").strip()
        if product_url:
            QDesktopServices.openUrl(QUrl(product_url))

    def _show_fashion_product_details(self, product: Any) -> None:
        FashionProductDetailsDialog(self.window() or self, self.manager, product).exec()

    def _fashion_product_for_row(self, row: int) -> Any | None:
        item = self.products_table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def _video_directory_settings() -> QSettings:
        return QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)

    def _load_video_directory(self) -> str:
        value = self._video_directory_settings().value(_VIDEO_DIRECTORY_KEY, "")
        return str(value or "").strip()

    def _save_video_directory(self) -> bool:
        directory_text = self.video_directory_edit.text().strip().strip('"')
        settings = self._video_directory_settings()
        if not directory_text:
            settings.remove(_VIDEO_DIRECTORY_KEY)
            settings.sync()
            return True

        directory = _existing_video_directory(directory_text)
        if directory is None:
            InfoBar.warning(
                title="Thư mục không tồn tại",
                content="Hãy chọn một thư mục video hợp lệ.",
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self.window(),
            )
            return False

        normalized = str(directory)
        self.video_directory_edit.setText(normalized)
        settings.setValue(_VIDEO_DIRECTORY_KEY, normalized)
        settings.sync()
        return True

    def _video_dialog_directory(self) -> str:
        configured = _existing_video_directory(self.video_directory_edit.text())
        if configured is not None:
            return str(configured)
        if self._video_file_path is not None and self._video_file_path.parent.is_dir():
            return str(self._video_file_path.parent.resolve())
        return ""

    def _choose_video_directory(self, _checked: bool = False) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục video mặc định",
            self._video_dialog_directory(),
        )
        if not directory:
            return
        self.video_directory_edit.setText(directory)
        self._save_video_directory()

    def _choose_video_file(self, _checked: bool = False) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn video để gửi sang điện thoại",
            self._video_dialog_directory(),
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.3gp);;All files (*.*)",
        )
        if not file_name:
            return
        self._video_file_path = Path(file_name)
        self.video_file_label.setText(self._video_file_path.name)
        self.send_video_button.setEnabled(True)

    def _send_video_to_phone(self) -> None:
        if not self._video_file_path:
            return
        phone_settings = load_phone_control_settings()
        connection_mode = str(getattr(phone_settings, "connection_mode", "wifi") or "wifi")
        if connection_mode == "wifi" and not str(phone_settings.address or "").strip():
            InfoBar.warning(
                title="Chưa có kết nối điện thoại",
                content="Hãy nhập IP hoặc chuyển sang USB trong Phone Control trước khi gửi video.",
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self.window(),
            )
            return

        config = getattr(self.window(), "config", None) or PipelineConfig.from_env()
        video_path = self._video_file_path
        remote_file_name = "%s_%s%s" % (
            video_path.stem,
            datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            video_path.suffix.lower(),
        )
        self.send_video_button.setEnabled(False)
        self.send_video_button.setText("Đang gửi video...")

        def _transfer() -> dict[str, object]:
            controller = PhoneController(config)
            return controller.send_file_to_gallery(
                str(phone_settings.address or ""),
                video_path,
                connection_mode=connection_mode,
                remote_file_name=remote_file_name,
            )

        self._video_transfer_worker = WorkerThread(_transfer, parent=self)
        self._video_transfer_worker.finished_task.connect(self._on_video_transfer_finished)
        self._video_transfer_worker.error_task.connect(self._on_video_transfer_error)
        self._video_transfer_worker.finished.connect(self._clear_video_transfer_worker)
        self._video_transfer_worker.start()

    def _on_video_transfer_finished(self, result: object) -> None:
        remote_path = str((result or {}).get("remote_path") or "") if isinstance(result, dict) else ""
        InfoBar.success(
            title="Đã gửi video",
            content=f"Video đã được thêm vào Gallery điện thoại. {remote_path}",
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self.window(),
        )

    def _on_video_transfer_error(self, error: Exception, _traceback: str) -> None:
        InfoBar.error(
            title="Không thể gửi video",
            content=str(error),
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=self.window(),
        )

    def _clear_video_transfer_worker(self) -> None:
        self._video_transfer_worker = None
        self.send_video_button.setEnabled(self._video_file_path is not None)
        self.send_video_button.setText("Gửi sang điện thoại")

    def _on_garment_changed(self, _index: int) -> None:
        preset = self._current_garment_preset()
        self._set_garment_prompts(load_garment_prompts(preset))

    def _current_garment_preset(self):
        key = str(self.garment_combo.currentData() or "")
        for preset in self._garment_presets:
            if preset.key == key:
                return preset
        return self._garment_presets[0]

    def _add_garment_type(self) -> None:
        label, accepted = QInputDialog.getText(
            self,
            "Thêm loại",
            "Tên loại mới:",
        )
        if not accepted:
            return
        try:
            created = add_garment_preset(label)
        except ValueError as exc:
            InfoBar.warning(
                title="Không thể thêm loại",
                content=str(exc),
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self.window(),
            )
            return

        self._garment_presets = load_garment_presets()
        self.garment_combo.blockSignals(True)
        self.garment_combo.clear()
        for preset in self._garment_presets:
            self.garment_combo.addItem(preset.label, userData=preset.key)
        self.garment_combo.setCurrentIndex(self.garment_combo.findData(created.key))
        self.garment_combo.blockSignals(False)
        self._on_garment_changed(self.garment_combo.currentIndex())
        InfoBar.success(
            title="Đã thêm loại",
            content=f"{created.label} đã sẵn sàng. Bạn có thể thêm prompt đầu tiên.",
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self.window(),
        )

    def _set_garment_prompts(self, prompts: list[tuple[str, str]]) -> None:
        for card, _name, _content in self._garment_prompt_widgets:
            self.garment_prompts_layout.removeWidget(card)
            card.deleteLater()
        self._garment_prompt_widgets.clear()
        for name, content in prompts:
            self._append_garment_prompt(name, content)

    def _refresh_change_outfit_prompt_card(self) -> None:
        _name, content = self._change_outfit_prompt
        self.change_outfit_preview.setText(content)
        self.change_outfit_preview.setToolTip(content)

    def _edit_change_outfit_prompt(self, _checked: bool = False) -> None:
        name, content = self._change_outfit_prompt
        dialog = PromptEditorDialog(
            self.window() or self,
            name,
            content,
            content_only=True,
        )
        if dialog.exec() and dialog.result_data is not None:
            self._change_outfit_prompt = dialog.result_data
            save_change_outfit_prompt(*dialog.result_data)
            self._refresh_change_outfit_prompt_card()

    def _on_add_garment_prompt(self, _checked: bool = False) -> None:
        dialog = PromptEditorDialog(self.window() or self)
        if dialog.exec() and dialog.result_data is not None:
            self._append_garment_prompt(*dialog.result_data)
            self._persist_current_garment_prompts()

    def _append_garment_prompt(self, name: str, content: str) -> None:
        prompt_card = CardWidget(self.garment_prompts_container)
        prompt_card.setObjectName("fashionPromptCard")
        prompt_layout = QVBoxLayout(prompt_card)
        prompt_layout.setContentsMargins(14, 12, 14, 12)
        prompt_layout.setSpacing(8)
        prompt_header = QHBoxLayout()
        title_label = BodyLabel(name, prompt_card)
        title_label.setObjectName("fashionPromptTitle")
        title_label.setToolTip(name)
        prompt_header.addWidget(title_label, 1)

        copy_button = self._prompt_action_button(prompt_card, FIF.COPY, "Sao chép prompt")
        copy_button.clicked.connect(lambda: self._copy_prompt_text(content))
        prompt_header.addWidget(copy_button)
        edit_button = self._prompt_action_button(prompt_card, FIF.EDIT, "Chỉnh sửa prompt")
        edit_button.clicked.connect(lambda: self._edit_garment_prompt(prompt_card))
        prompt_header.addWidget(edit_button)
        delete_button = self._prompt_action_button(prompt_card, FIF.DELETE, "Xóa prompt")
        delete_button.clicked.connect(lambda: self._remove_garment_prompt(prompt_card))
        prompt_header.addWidget(delete_button)
        prompt_layout.addLayout(prompt_header)

        preview = BodyLabel(self._prompt_preview(content), prompt_card)
        preview.setObjectName("fashionPromptPreview")
        preview.setWordWrap(True)
        preview.setToolTip(content)
        prompt_layout.addWidget(preview, 1)
        prompt_card.setFixedHeight(150)
        self._apply_prompt_card_theme(prompt_card, get_current_theme_mode())

        index = len(self._garment_prompt_widgets)
        self.garment_prompts_layout.addWidget(prompt_card, index // 4, index % 4)
        self._garment_prompt_widgets.append((prompt_card, name, content))

    @staticmethod
    def _prompt_preview(content: str, limit: int = 180) -> str:
        normalized = " ".join(content.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"

    @staticmethod
    def _prompt_action_button(parent: QWidget, icon, tooltip: str) -> ToolButton:
        button = ToolButton(parent)
        button.setIcon(icon)
        button.setFixedSize(28, 28)
        button.setToolTip(tooltip)
        return button

    @staticmethod
    def _apply_prompt_card_theme(card: CardWidget, mode: str) -> None:
        dark = str(mode).strip().lower() == "dark"
        background = DARK_CARD_HOVER if dark else LIGHT_CARD
        hover_background = "#282D3B" if dark else LIGHT_CARD_HOVER
        border = DARK_BORDER if dark else LIGHT_BORDER
        title_color = DARK_TEXT_MAIN if dark else LIGHT_TEXT_MAIN
        preview_color = DARK_TEXT_SECONDARY if dark else LIGHT_TEXT_SECONDARY
        card.setStyleSheet(
            "QWidget#fashionPromptCard { background-color: %s; border: 1px solid %s; border-radius: 10px; }"
            " QWidget#fashionPromptCard:hover { background-color: %s; border-color: %s; }"
            % (background, border, hover_background, border)
        )
        for title in card.findChildren(BodyLabel, "fashionPromptTitle"):
            title.setStyleSheet("font-weight: 600; color: %s;" % title_color)
        for preview in card.findChildren(BodyLabel, "fashionPromptPreview"):
            preview.setStyleSheet("color: %s;" % preview_color)

    def _edit_garment_prompt(self, prompt_card: CardWidget) -> None:
        for index, (card, name, content) in enumerate(self._garment_prompt_widgets):
            if card is not prompt_card:
                continue
            dialog = PromptEditorDialog(self.window() or self, name, content)
            if dialog.exec() and dialog.result_data is not None:
                self._garment_prompt_widgets[index] = (card, *dialog.result_data)
                self._rebuild_garment_prompt_cards()
                self._persist_current_garment_prompts()
            return

    def _rebuild_garment_prompt_cards(self) -> None:
        prompts = [(name, content) for _card, name, content in self._garment_prompt_widgets]
        self._set_garment_prompts(prompts)

    def _remove_garment_prompt(self, prompt_card: CardWidget) -> None:
        for index, (card, _name, _content) in enumerate(self._garment_prompt_widgets):
            if card is prompt_card:
                self._garment_prompt_widgets.pop(index)
                self.garment_prompts_layout.removeWidget(card)
                card.deleteLater()
                self._rebuild_garment_prompt_cards()
                self._persist_current_garment_prompts()
                return

    def _collect_garment_prompts(self) -> list[tuple[str, str]] | None:
        return [(name, content) for _card, name, content in self._garment_prompt_widgets]

    def _persist_current_garment_prompts(self) -> None:
        preset = self._current_garment_preset()
        save_garment_prompts(preset, self._collect_garment_prompts() or [])

    def _save_preset_prompts(self) -> None:
        preset = self._current_garment_preset()
        self._persist_current_garment_prompts()
        InfoBar.success(
            title="Đã lưu prompt",
            content=f"Danh sách prompt {preset.label} đã được lưu cục bộ.",
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self.window(),
        )

    def _copy_prompt_text(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(prompt)
        InfoBar.success(
            title="Đã sao chép prompt",
            content="Prompt đã được sao chép vào clipboard.",
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self.window(),
        )

    def apply_theme_mode(self, mode: str) -> None:
        """Refresh Fashion-specific colours when the application theme changes."""
        self._apply_fashion_tabs_theme(mode)
        self._apply_prompt_card_theme(self.change_outfit_card, mode)
        for card, _name, _content in self._garment_prompt_widgets:
            self._apply_prompt_card_theme(card, mode)

    def _apply_fashion_tabs_theme(self, mode: str) -> None:
        """Keep native Qt tabs consistent with the application's selected theme."""
        dark = str(mode).strip().lower() == "dark"
        if dark:
            page_background = "#11131A"
            border = "#303543"
            selected_border = "#8B7CFF"
            text = "#B5B9C7"
            selected_text = "#F3F4F8"
        else:
            page_background = "#F6F7FB"
            border = "#DDE1EA"
            selected_border = "#6D5DFB"
            text = "#5F6475"
            selected_text = "#181B2A"

        self.fashion_tabs.setStyleSheet(
            "QTabWidget#fashionTabs { background-color: %s; }"
            " QTabWidget#fashionTabs::pane {"
            " background-color: %s; border: none;"
            " }"
            " QTabWidget#fashionTabs::tab-bar { alignment: left; }"
            " QTabBar { background: transparent; }"
            " QTabBar::tab {"
            " background-color: transparent; color: %s; border: none;"
            " margin-right: 12px; min-width: 132px;"
            " padding: 10px 18px 8px; font-weight: 600;"
            " }"
            " QTabBar::tab:hover { color: %s; }"
            " QTabBar::tab:selected {"
            " background-color: transparent; color: %s;"
            " border-bottom: 2px solid %s;"
            " }"
            " QWidget#fashionProductsTab, QWidget#fashionScrollContent,"
            " QScrollArea#fashionScrollArea, QWidget#fashionScrollViewport {"
            " background-color: %s;"
            " }"
            " CardWidget#fashionProductsCard { border-color: %s; }"
            % (
                page_background,
                page_background,
                text,
                selected_text,
                selected_text,
                selected_border,
                page_background,
                border,
            )
        )

    def shutdown(self) -> None:
        if hasattr(self, "_fashion_sync_timer"):
            self._fashion_sync_timer.stop()
        workers = list(self._fashion_job_workers)
        self._fashion_job_workers.clear()
        transfer_worker = self._video_transfer_worker
        self._video_transfer_worker = None
        if transfer_worker is not None and transfer_worker not in workers:
            workers.append(transfer_worker)
        for worker in workers:
            worker.stop(timeout_ms=1500)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
