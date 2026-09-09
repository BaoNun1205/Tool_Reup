"""Facebook Pages view backed by the official Meta Graph API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    PasswordLineEdit,
    LineEdit,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    ToolButton,
)

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.facebook.client import FacebookGraphClient
from auto_tiktok_editor.facebook.exceptions import FacebookGraphError
from auto_tiktok_editor.facebook.models import FacebookPage
from auto_tiktok_editor.facebook.queue import prepare_facebook_video, send_facebook_video_to_phone
from auto_tiktok_editor.phone_control import load_phone_control_settings
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.empty_state_table import EmptyStateTableWidget
from auto_tiktok_editor.tiktok_profiles.qt_ui.components.tag_bar import YouTubeTagInput
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import format_vietnam_datetime, get_current_theme_mode
from auto_tiktok_editor.tiktok_profiles.qt_ui.workers import WorkerThread

_LOGGER = logging.getLogger("auto_tiktok_editor.facebook")
_GRAPH_API_EXPLORER_URL = "https://developers.facebook.com/tools/explorer/"


class FacebookPageCard(CardWidget):
    """Responsive summary card for one managed Facebook Page."""

    def __init__(self, page: FacebookPage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self.setObjectName("facebookPageCard")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)

        self.avatar_label = QLabel(self)
        self.avatar_label.setObjectName("facebookPageAvatar")
        self.avatar_label.setFixedSize(56, 56)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setText((page.name[:1] or "F").upper())
        layout.addWidget(self.avatar_label, alignment=Qt.AlignmentFlag.AlignTop)

        details = QVBoxLayout()
        details.setSpacing(5)
        name_label = SubtitleLabel(page.name, self)
        name_label.setWordWrap(True)
        details.addWidget(name_label)

        self.metadata_label = BodyLabel(
            f"Page ID: {page.page_id}\nCategory: {page.category or 'Chưa có thông tin'}",
            self,
        )
        self.metadata_label.setObjectName("facebookPageMetadata")
        self.metadata_label.setWordWrap(True)
        details.addWidget(self.metadata_label)

        task_text = ", ".join(page.tasks) if page.tasks else "Chưa có thông tin"
        self.tasks_label = BodyLabel(f"Quyền: {task_text}", self)
        self.tasks_label.setObjectName("facebookPageTasks")
        self.tasks_label.setWordWrap(True)
        details.addWidget(self.tasks_label)
        layout.addLayout(details, 1)

        open_button = PushButton("Mở Page", self, FIF.GLOBE)
        open_button.setMinimumWidth(116)
        open_button.clicked.connect(self._open_page)
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme_mode(get_current_theme_mode())

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(f"https://www.facebook.com/{self.page.page_id}"))

    def set_avatar(self, source: QPixmap) -> None:
        if source.isNull():
            return
        size = self.avatar_label.size()
        scaled = source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        rounded = QPixmap(size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(rounded.rect())
        painter.setClipPath(path)
        x = (scaled.width() - size.width()) // 2
        y = (scaled.height() - size.height()) // 2
        painter.drawPixmap(0, 0, scaled, x, y, size.width(), size.height())
        painter.end()
        self.avatar_label.setText("")
        self.avatar_label.setPixmap(rounded)

    def apply_theme_mode(self, mode: str) -> None:
        dark = str(mode).strip().lower() == "dark"
        muted = "#B5B9C7" if dark else "#5F6475"
        avatar_bg = "#27233F" if dark else "#EEECFF"
        avatar_fg = "#A69BFF" if dark else "#5B4CF0"
        self.metadata_label.setStyleSheet(f"color: {muted};")
        self.tasks_label.setStyleSheet(f"color: {muted};")
        self.avatar_label.setStyleSheet(
            "QLabel#facebookPageAvatar {"
            f" background-color: {avatar_bg}; color: {avatar_fg};"
            " border-radius: 28px; font-size: 20px; font-weight: 700;"
            "}"
        )


class FacebookView(QWidget):
    """Connect Pages and manage the independent Facebook video queue."""

    def __init__(
        self,
        manager: TikTokProfileManager,
        config: PipelineConfig | None = None,
        parent: QWidget | None = None,
        *,
        client: FacebookGraphClient | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.config = config or PipelineConfig.from_env()
        self.client = client or FacebookGraphClient()
        self._user_access_token = ""
        self._worker: WorkerThread | None = None
        self._page_cards: list[FacebookPageCard] = []
        self._avatar_replies: dict[QNetworkReply, FacebookPageCard] = {}
        self._queue_cache = []
        self._queue_signature: tuple | None = None
        self._selected_queue_video = None
        self._select_mode = False
        self._updating_editor = False
        self._queue_workers: list[WorkerThread] = []
        self._sending_video_ids: set[int] = set()
        self._preparing_video_ids: set[int] = set()
        self._shutting_down = False
        self._network_manager = QNetworkAccessManager(self)
        self._init_ui()
        self.manager.recover_interrupted_facebook_videos()
        self._queue_timer = QTimer(self)
        self._queue_timer.setInterval(1000)
        self._queue_timer.timeout.connect(self.refresh_queue)
        self.apply_theme_mode(get_current_theme_mode())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_queue()
        if not self._shutting_down and not self._queue_timer.isActive():
            self._queue_timer.start()

    def hideEvent(self, event) -> None:
        self._queue_timer.stop()
        super().hideEvent(event)

    def _init_ui(self) -> None:
        # Keep the page itself fixed. The queue table owns scrolling so moving
        # through many videos does not move the Facebook connection controls.
        container = QWidget(self)
        container.setObjectName("facebookScrollContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addWidget(SubtitleLabel("Facebook Pages", container))
        description = BodyLabel("Quản lý các Page Facebook của bạn", container)
        description.setWordWrap(True)
        layout.addWidget(description)

        connection_card = CardWidget(container)
        connection_layout = QVBoxLayout(connection_card)
        connection_layout.setContentsMargins(20, 18, 20, 18)
        connection_layout.setSpacing(12)
        connection_layout.addWidget(SubtitleLabel("Kết nối Facebook", connection_card))

        token_row = QHBoxLayout()
        token_row.setSpacing(10)
        self.token_edit = PasswordLineEdit(connection_card)
        self.token_edit.setEchoMode(PasswordLineEdit.EchoMode.Password)
        self.token_edit.setViewPasswordButtonVisible(False)
        self.token_edit.setPlaceholderText("User Access Token")
        self.token_edit.returnPressed.connect(self._connect_facebook)
        token_row.addWidget(self.token_edit, 1)

        self.connect_button = PrimaryPushButton(
            "Kết nối Facebook", connection_card, FIF.LINK
        )
        self.connect_button.clicked.connect(self._connect_facebook)
        token_row.addWidget(self.connect_button)
        connection_layout.addLayout(token_row)

        helper_row = QHBoxLayout()
        self.token_help_label = CaptionLabel(
            "Token chỉ được dùng để tải danh sách Page mà tài khoản này có quyền quản lý.",
            connection_card,
        )
        self.token_help_label.setWordWrap(True)
        helper_row.addWidget(self.token_help_label, 1)
        explorer_button = PushButton(
            "Mở Graph API Explorer", connection_card, FIF.DEVELOPER_TOOLS
        )
        explorer_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_GRAPH_API_EXPLORER_URL))
        )
        helper_row.addWidget(explorer_button)
        connection_layout.addLayout(helper_row)
        layout.addWidget(connection_card)

        status_card = CardWidget(container)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(18, 14, 18, 14)
        status_layout.setSpacing(10)
        self.progress_ring = IndeterminateProgressRing(status_card, start=False)
        self.progress_ring.setFixedSize(24, 24)
        self.progress_ring.hide()
        status_layout.addWidget(self.progress_ring)
        self.status_label = BodyLabel("Chưa kết nối Facebook", status_card)
        self.status_label.setObjectName("facebookStatusLabel")
        status_layout.addWidget(self.status_label, 1)
        self.refresh_button = PushButton("Làm mới", status_card, FIF.SYNC)
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self._refresh_pages)
        status_layout.addWidget(self.refresh_button)
        layout.addWidget(status_card)

        self.pages_container = QWidget(container)
        self.pages_container.setObjectName("facebookPagesContainer")
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setContentsMargins(0, 0, 0, 0)
        self.pages_layout.setSpacing(12)
        layout.addWidget(self.pages_container)

        layout.addWidget(SubtitleLabel("Video Facebook", container))
        queue_toolbar = QHBoxLayout()
        queue_toolbar.setSpacing(10)
        self.queue_refresh_button = PushButton("Làm mới", container, FIF.SYNC)
        self.queue_refresh_button.clicked.connect(lambda: self.refresh_queue(force=True))
        queue_toolbar.addWidget(self.queue_refresh_button)
        self.queue_select_button = PushButton("Chọn", container, FIF.CHECKBOX)
        self.queue_select_button.clicked.connect(self._toggle_queue_select_mode)
        queue_toolbar.addWidget(self.queue_select_button)
        self.queue_delete_button = PushButton("Xóa", container, FIF.DELETE)
        self.queue_delete_button.clicked.connect(lambda: self._delete_queue_videos(None))
        queue_toolbar.addWidget(self.queue_delete_button)
        queue_toolbar.addStretch(1)
        layout.addLayout(queue_toolbar)

        queue_splitter = QSplitter(Qt.Orientation.Horizontal, container)
        queue_splitter.setChildrenCollapsible(False)
        queue_splitter.setMinimumHeight(280)

        self.queue_table = EmptyStateTableWidget(
            queue_splitter,
            empty_text="Chưa có video nào trong hàng đợi Facebook.",
            empty_icon=FIF.VIDEO,
        )
        self.queue_table.setColumnCount(7)
        self.queue_table.setHorizontalHeaderLabels(
            ["☐", "Ngày tạo", "Tên sản phẩm", "Caption", "Hashtag", "Trạng thái", "Hành động"]
        )
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setColumnWidth(0, 36)
        self.queue_table.setColumnWidth(1, 145)
        self.queue_table.setColumnWidth(5, 110)
        self.queue_table.setColumnWidth(6, 150)
        self.queue_table.setColumnHidden(0, True)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setShowGrid(False)
        self.queue_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.queue_table.verticalHeader().setDefaultSectionSize(44)
        self.queue_table.itemSelectionChanged.connect(self._on_queue_selection_changed)
        self.queue_table.itemChanged.connect(self._on_queue_item_changed)
        self.queue_table.horizontalHeader().sectionClicked.connect(self._on_queue_header_clicked)
        queue_splitter.addWidget(self.queue_table)

        editor_scroll = QScrollArea(queue_splitter)
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        editor_card = CardWidget(editor_scroll)
        editor_card.setMinimumWidth(285)
        editor_card.setMinimumHeight(455)
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(16, 14, 16, 14)
        editor_layout.setSpacing(6)
        editor_layout.addWidget(SubtitleLabel("Chi tiết Facebook", editor_card))
        self.queue_file_label = BodyLabel("Chưa chọn video", editor_card)
        editor_layout.addWidget(self.queue_file_label)
        editor_layout.addWidget(BodyLabel("Tên sản phẩm hiển thị:", editor_card))
        self.queue_product_name_edit = LineEdit(editor_card)
        self.queue_product_name_edit.setMaxLength(50)
        self.queue_product_name_edit.setPlaceholderText("Tối đa 50 ký tự")
        self.queue_product_name_edit.textChanged.connect(self._update_product_name_count)
        editor_layout.addWidget(self.queue_product_name_edit)
        self.queue_product_name_count = CaptionLabel("0/50 ký tự", editor_card)
        editor_layout.addWidget(self.queue_product_name_count)
        self.queue_regenerate_button = PushButton("Gemini viết lại tên", editor_card, FIF.SYNC)
        self.queue_regenerate_button.clicked.connect(self._regenerate_selected_name)
        editor_layout.addWidget(self.queue_regenerate_button)
        editor_layout.addWidget(BodyLabel("Caption:", editor_card))
        self.queue_caption_edit = PlainTextEdit(editor_card)
        self.queue_caption_edit.setFixedHeight(72)
        editor_layout.addWidget(self.queue_caption_edit)
        editor_layout.addWidget(BodyLabel("Hashtag:", editor_card))
        self.queue_tag_input = YouTubeTagInput("", editor_card)
        self.queue_tag_input.setMinimumHeight(110)
        editor_layout.addWidget(self.queue_tag_input)
        self.queue_note_label = CaptionLabel("", editor_card)
        self.queue_note_label.setWordWrap(True)
        editor_layout.addWidget(self.queue_note_label)
        self.queue_save_button = PrimaryPushButton("Lưu thay đổi", editor_card, FIF.SAVE)
        self.queue_save_button.clicked.connect(self._save_queue_details)
        editor_layout.addWidget(self.queue_save_button)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor_card)
        queue_splitter.addWidget(editor_scroll)
        queue_splitter.setStretchFactor(0, 3)
        queue_splitter.setStretchFactor(1, 2)
        queue_splitter.setSizes([700, 340])
        layout.addWidget(queue_splitter, 1)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(container)

    @Slot()
    def _connect_facebook(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            InfoBar.warning(
                "Thiếu Access Token",
                "Vui lòng nhập User Access Token để kết nối Facebook.",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return
        self._user_access_token = token
        self.token_edit.clear()
        self._load_pages(token)

    @Slot()
    def _refresh_pages(self) -> None:
        if self._user_access_token:
            self._load_pages(self._user_access_token)

    def _load_pages(self, token: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_loading(True)
        worker = WorkerThread(self.client.get_managed_pages, token, parent=self)
        self._worker = worker
        worker.finished_task.connect(self._on_pages_loaded)
        worker.error_task.connect(self._on_load_error)
        worker.finished.connect(lambda: self._release_worker(worker))
        worker.start()

    def _set_loading(self, loading: bool) -> None:
        self.connect_button.setEnabled(not loading)
        self.refresh_button.setEnabled(not loading and bool(self._user_access_token))
        if loading:
            self.status_label.setText("Đang tải danh sách Page...")
            self.progress_ring.show()
            self.progress_ring.start()
        else:
            self.progress_ring.stop()
            self.progress_ring.hide()

    @Slot(object)
    def _on_pages_loaded(self, result: object) -> None:
        if self._shutting_down:
            return
        pages = list(result) if isinstance(result, (list, tuple)) else []
        self._set_loading(False)
        self._show_pages(pages)
        if pages:
            self.status_label.setText(
                f"Tài khoản đã kết nối  •  Đã tìm thấy: {len(pages)} Page"
            )
            InfoBar.success(
                "Kết nối thành công",
                f"Đã tải {len(pages)} Page Facebook.",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
        else:
            self.status_label.setText("Tài khoản đã kết nối  •  Đã tìm thấy: 0 Page")
            InfoBar.warning(
                "Không có Page",
                "Không tìm thấy Page nào mà tài khoản này có quyền quản lý.",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    @Slot(Exception, str)
    def _on_load_error(self, exc: Exception, _traceback_text: str) -> None:
        if self._shutting_down:
            return
        self._set_loading(False)
        message = str(exc) if isinstance(exc, FacebookGraphError) else (
            "Không thể tải danh sách Page Facebook."
        )
        self.status_label.setText(message)
        _LOGGER.error("Facebook Page load failed (%s).", type(exc).__name__)
        InfoBar.error(
            "Lỗi kết nối Facebook",
            message,
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _release_worker(self, worker: WorkerThread) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _show_pages(self, pages: list[FacebookPage]) -> None:
        self._clear_page_cards()
        for page in pages:
            card = FacebookPageCard(page, self.pages_container)
            self._page_cards.append(card)
            self.pages_layout.addWidget(card)
            if page.picture_url:
                self._load_avatar(page.picture_url, card)

    def _clear_page_cards(self) -> None:
        for reply in tuple(self._avatar_replies):
            reply.abort()
            reply.deleteLater()
        self._avatar_replies.clear()
        self._page_cards.clear()
        while self.pages_layout.count():
            item = self.pages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _load_avatar(self, url: str, card: FacebookPageCard) -> None:
        parsed_url = QUrl(url)
        if not parsed_url.isValid() or parsed_url.scheme().lower() != "https":
            return
        request = QNetworkRequest(parsed_url)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._network_manager.get(request)
        self._avatar_replies[reply] = card
        reply.finished.connect(lambda reply=reply: self._on_avatar_loaded(reply))

    def _on_avatar_loaded(self, reply: QNetworkReply) -> None:
        card = self._avatar_replies.pop(reply, None)
        if (
            card is not None
            and not self._shutting_down
            and reply.error() == QNetworkReply.NetworkError.NoError
        ):
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(reply.readAll())):
                card.set_avatar(pixmap)
        reply.deleteLater()

    def refresh_queue(self, force: bool = False) -> None:
        if self._shutting_down:
            return
        try:
            videos = self.manager.list_facebook_videos()
            signature = tuple(
                (
                    item.id,
                    item.file_path,
                    item.display_product_name,
                    item.caption,
                    item.hashtags,
                    item.status,
                    item.note,
                    item.updated_at,
                    item.id in self._sending_video_ids,
                    item.id in self._preparing_video_ids,
                )
                for item in videos
            )
            if not force and signature == self._queue_signature:
                return
            selected_id = self._selected_queue_video.id if self._selected_queue_video else None
            checked_ids = {
                item.data(Qt.ItemDataRole.UserRole).id
                for row in range(self.queue_table.rowCount())
                if (item := self.queue_table.item(row, 0)) is not None
                and item.checkState() == Qt.CheckState.Checked
                and item.data(Qt.ItemDataRole.UserRole) is not None
            }
            scroll_value = self.queue_table.verticalScrollBar().value()
            self._queue_cache = videos
            self._queue_signature = signature
            self._populate_queue_table(videos, checked_ids)
            self.queue_table.verticalScrollBar().setValue(scroll_value)
            if selected_id is not None:
                for row in range(self.queue_table.rowCount()):
                    item = self.queue_table.item(row, 0)
                    video = item.data(Qt.ItemDataRole.UserRole) if item else None
                    if video is not None and video.id == selected_id:
                        self.queue_table.selectRow(row)
                        self._selected_queue_video = video
                        break
        except Exception as exc:
            if force:
                InfoBar.error("Không thể tải video Facebook", str(exc), parent=self.window())

    def _populate_queue_table(self, videos: list[Any], checked_ids: set[int]) -> None:
        self.queue_table.blockSignals(True)
        self.queue_table.setUpdatesEnabled(False)
        for row in range(self.queue_table.rowCount()):
            widget = self.queue_table.cellWidget(row, 6)
            if widget is not None:
                self.queue_table.removeCellWidget(row, 6)
                widget.deleteLater()
        self.queue_table.setRowCount(len(videos))
        for row, video in enumerate(videos):
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if video.id in checked_ids else Qt.CheckState.Unchecked
            )
            check_item.setData(Qt.ItemDataRole.UserRole, video)
            created_item = QTableWidgetItem(format_vietnam_datetime(video.created_at))
            created_item.setData(Qt.ItemDataRole.UserRole, video)
            product_item = QTableWidgetItem(video.display_product_name or "Chưa có tên")
            product_item.setToolTip(video.source_product_name or video.note)
            caption_item = QTableWidgetItem(video.caption)
            hashtags_item = QTableWidgetItem(video.hashtags)
            status_text, status_color = self._facebook_status(video)
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(status_color)

            self.queue_table.setItem(row, 0, check_item)
            self.queue_table.setItem(row, 1, created_item)
            self.queue_table.setItem(row, 2, product_item)
            self.queue_table.setItem(row, 3, caption_item)
            self.queue_table.setItem(row, 4, hashtags_item)
            self.queue_table.setItem(row, 5, status_item)

            actions = QWidget(self.queue_table)
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            preparing = video.id in self._preparing_video_ids or video.status == "processing"
            sending = video.id in self._sending_video_ids
            regenerate = ToolButton(actions)
            regenerate.setIcon(FIF.SYNC)
            regenerate.setFixedSize(28, 28)
            regenerate.setEnabled(not preparing and not sending)
            regenerate.setToolTip("Gemini viết lại tên sản phẩm")
            regenerate.clicked.connect(lambda _checked=False, item=video: self._regenerate_name(item))
            action_layout.addWidget(regenerate)

            send_container = QWidget(actions)
            send_container.setFixedSize(28, 28)
            send_layout = QHBoxLayout(send_container)
            send_layout.setContentsMargins(0, 0, 0, 0)
            send_button = ToolButton(send_container)
            send_button.setIcon(FIF.SEND)
            send_button.setFixedSize(28, 28)
            send_button.setEnabled(bool(video.display_product_name) and not preparing and not sending)
            send_button.setToolTip("Gửi video, caption/hashtag và tên sản phẩm sang điện thoại")
            send_button.clicked.connect(lambda _checked=False, item=video: self._send_queue_video(item))
            send_layout.addWidget(send_button)
            spinner = IndeterminateProgressRing(send_container, start=False)
            spinner.setFixedSize(20, 20)
            spinner.setStrokeWidth(3)
            send_layout.addWidget(spinner)
            if sending:
                send_button.hide()
                spinner.show()
                spinner.start()
            else:
                spinner.hide()
            action_layout.addWidget(send_container)

            play_button = ToolButton(actions)
            play_button.setIcon(FIF.PLAY)
            play_button.setFixedSize(28, 28)
            play_button.setToolTip("Xem video")
            play_button.clicked.connect(lambda _checked=False, item=video: self._play_queue_video(item))
            action_layout.addWidget(play_button)

            delete_button = ToolButton(actions)
            delete_button.setIcon(FIF.DELETE)
            delete_button.setFixedSize(28, 28)
            delete_button.setEnabled(not preparing and not sending)
            delete_button.setToolTip("Xóa khỏi bảng Facebook")
            delete_button.clicked.connect(lambda _checked=False, item=video: self._delete_queue_videos(item))
            action_layout.addWidget(delete_button)
            self.queue_table.setCellWidget(row, 6, actions)

        self.queue_table.blockSignals(False)
        self.queue_table.setUpdatesEnabled(True)
        self.queue_table.viewport().update()
        self.queue_table.setColumnHidden(0, not self._select_mode)
        self._update_queue_toolbar()

    @staticmethod
    def _facebook_status(video: Any) -> tuple[str, Any]:
        from PySide6.QtGui import QColor

        status = str(video.status or "").lower()
        mapping = {
            "processing": ("Đang xử lý", "#D99A2B"),
            "ready": ("Sẵn sàng", "#22A06B"),
            "sent": ("Đã gửi", "#3977E3"),
            "error": ("Lỗi", "#D64545"),
        }
        text, color = mapping.get(status, (status or "Không rõ", "#777777"))
        return text, QColor(color)

    def _on_queue_selection_changed(self) -> None:
        selected = self._selected_queue_items(include_checked=False)
        self._selected_queue_video = selected[0] if selected else None
        self._updating_editor = True
        try:
            video = self._selected_queue_video
            if video is None:
                self.queue_file_label.setText("Chưa chọn video")
                self.queue_product_name_edit.clear()
                self.queue_caption_edit.clear()
                self.queue_tag_input.set_tags("")
                self.queue_note_label.clear()
            else:
                self.queue_file_label.setText(Path(video.file_path).name)
                self.queue_product_name_edit.setText(video.display_product_name)
                self.queue_caption_edit.setPlainText(video.caption)
                self.queue_tag_input.set_tags(video.hashtags)
                self.queue_note_label.setText(video.note)
        finally:
            self._updating_editor = False
        self._update_queue_toolbar()

    def _update_product_name_count(self, text: str) -> None:
        self.queue_product_name_count.setText(f"{len(text)}/50 ký tự")

    def _save_queue_details(self) -> None:
        video = self._selected_queue_video
        if video is None:
            InfoBar.warning("Chưa chọn video", "Hãy chọn một video Facebook để lưu.", parent=self.window())
            return
        try:
            updated = self.manager.update_facebook_video_details(
                video.id,
                display_product_name=self.queue_product_name_edit.text(),
                caption=self.queue_caption_edit.toPlainText(),
                hashtags=self.queue_tag_input.get_tags_string(),
            )
            self._selected_queue_video = updated
            self.refresh_queue(force=True)
            InfoBar.success("Đã lưu", "Đã cập nhật video Facebook.", parent=self.window())
        except Exception as exc:
            InfoBar.error("Không thể lưu", str(exc), parent=self.window())

    def _toggle_queue_select_mode(self) -> None:
        self._select_mode = not self._select_mode
        self.queue_table.setColumnHidden(0, not self._select_mode)
        if not self._select_mode:
            self.queue_table.blockSignals(True)
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 0)
                if item:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.queue_table.blockSignals(False)
        self._update_queue_toolbar()

    def _on_queue_header_clicked(self, section: int) -> None:
        if section != 0 or not self._select_mode:
            return
        all_checked = all(
            (item := self.queue_table.item(row, 0)) is not None
            and item.checkState() == Qt.CheckState.Checked
            for row in range(self.queue_table.rowCount())
        )
        self.queue_table.blockSignals(True)
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked)
        self.queue_table.blockSignals(False)
        self._update_queue_toolbar()

    def _on_queue_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._update_queue_toolbar()

    def _update_queue_toolbar(self) -> None:
        selected_count = len(self._selected_queue_items())
        self.queue_select_button.setText("Hủy chọn" if self._select_mode else "Chọn")
        self.queue_delete_button.setText(f"Xóa ({selected_count})" if selected_count else "Xóa")

    def _selected_queue_items(self, *, include_checked: bool = True) -> list[Any]:
        selected: list[Any] = []
        seen: set[int] = set()
        if include_checked and self._select_mode:
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 0)
                video = item.data(Qt.ItemDataRole.UserRole) if item else None
                if item and item.checkState() == Qt.CheckState.Checked and video and video.id not in seen:
                    selected.append(video)
                    seen.add(video.id)
        if not selected:
            for index in self.queue_table.selectionModel().selectedRows():
                item = self.queue_table.item(index.row(), 0) or self.queue_table.item(index.row(), 1)
                video = item.data(Qt.ItemDataRole.UserRole) if item else None
                if video and video.id not in seen:
                    selected.append(video)
                    seen.add(video.id)
        return selected

    def _delete_queue_videos(self, single_video: Any | None) -> None:
        targets = [single_video] if single_video is not None else self._selected_queue_items()
        if not targets:
            InfoBar.warning("Chưa chọn video", "Hãy chọn video Facebook cần xóa.", parent=self.window())
            return
        active_ids = self._sending_video_ids | self._preparing_video_ids
        if any(video.id in active_ids for video in targets):
            InfoBar.warning(
                "Video đang được xử lý",
                "Hãy chờ thao tác đang chạy hoàn tất trước khi xóa.",
                parent=self.window(),
            )
            return
        box = MessageBox(
            "Xóa video Facebook",
            "Xóa %s video khỏi bảng Facebook? Video gốc trong tab Videos vẫn được giữ."
            % len(targets),
            self.window(),
        )
        if not box.exec():
            return
        report = self.manager.delete_facebook_videos([video.id for video in targets])
        if self._selected_queue_video and self._selected_queue_video.id in {item.id for item in targets}:
            self._selected_queue_video = None
        self.refresh_queue(force=True)
        if report["errors"]:
            InfoBar.warning("Xóa chưa hoàn tất", "\n".join(report["errors"]), parent=self.window())
        else:
            InfoBar.success("Đã xóa", "Đã xóa %s video Facebook." % report["deleted"], parent=self.window())

    def _regenerate_selected_name(self) -> None:
        if self._selected_queue_video is None:
            InfoBar.warning("Chưa chọn video", "Hãy chọn video cần viết lại tên.", parent=self.window())
            return
        self._regenerate_name(self._selected_queue_video)

    def _regenerate_name(self, video: Any) -> None:
        if video.id in self._preparing_video_ids:
            return
        self._preparing_video_ids.add(video.id)
        self.refresh_queue(force=True)

        def _work():
            try:
                return prepare_facebook_video(self.manager, video.id)
            except Exception as exc:
                self.manager.update_facebook_video_status(video.id, "error", note=str(exc))
                raise

        worker = WorkerThread(_work, parent=self)
        self._queue_workers.append(worker)

        def _finish() -> None:
            self._preparing_video_ids.discard(video.id)
            if worker in self._queue_workers:
                self._queue_workers.remove(worker)
            self.refresh_queue(force=True)

        def _done(updated: Any) -> None:
            _finish()
            InfoBar.success("Đã viết lại tên", updated.display_product_name, parent=self.window())

        def _error(exc: Exception, _traceback_text: str) -> None:
            _finish()
            InfoBar.error("Không thể viết lại tên", str(exc), parent=self.window())

        worker.finished_task.connect(_done)
        worker.error_task.connect(_error)
        worker.start()

    def _play_queue_video(self, video: Any) -> None:
        path = self.manager.resolve_facebook_video_path(video)
        if not path.is_file():
            InfoBar.error("Không tìm thấy video", str(path), parent=self.window())
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _send_queue_video(self, video: Any) -> None:
        if video.id in self._sending_video_ids:
            return
        if not video.display_product_name or len(video.display_product_name) > 50:
            InfoBar.warning(
                "Tên sản phẩm chưa hợp lệ",
                "Tên hiển thị phải có nội dung và không quá 50 ký tự.",
                parent=self.window(),
            )
            return
        settings = load_phone_control_settings()
        address = str(settings.address or "").strip()
        connection_mode = str(settings.connection_mode or "wifi")
        if connection_mode == "wifi" and not address:
            InfoBar.warning(
                "Chưa kết nối điện thoại",
                "Hãy nhập IP hoặc chuyển sang USB trong Phone Control trước khi gửi.",
                parent=self.window(),
            )
            return
        self._sending_video_ids.add(video.id)
        self.refresh_queue(force=True)

        def _work():
            return send_facebook_video_to_phone(
                self.manager,
                self.config,
                video.id,
                address=address,
                connection_mode=connection_mode,
            )

        worker = WorkerThread(_work, parent=self)
        self._queue_workers.append(worker)

        def _finish() -> None:
            self._sending_video_ids.discard(video.id)
            if worker in self._queue_workers:
                self._queue_workers.remove(worker)
            self.refresh_queue(force=True)

        def _done(_result: Any) -> None:
            _finish()
            InfoBar.success(
                "Gửi thành công",
                "Đã gửi video, caption/hashtag và tên sản phẩm sang điện thoại.",
                parent=self.window(),
            )

        def _error(exc: Exception, _traceback_text: str) -> None:
            _finish()
            InfoBar.error("Không thể gửi video Facebook", str(exc), parent=self.window())

        worker.finished_task.connect(_done)
        worker.error_task.connect(_error)
        worker.start()

    def apply_theme_mode(self, mode: str) -> None:
        dark = str(mode).strip().lower() == "dark"
        muted = "#B5B9C7" if dark else "#5F6475"
        self.token_help_label.setStyleSheet(f"color: {muted};")
        self.status_label.setStyleSheet(f"color: {muted}; font-weight: 600;")
        self.queue_note_label.setStyleSheet(f"color: {muted};")
        self.queue_product_name_count.setStyleSheet(f"color: {muted};")
        self.queue_tag_input.set_theme_mode("dark" if dark else "light")
        for card in self._page_cards:
            card.apply_theme_mode(mode)

    def shutdown(self) -> None:
        self._shutting_down = True
        if hasattr(self, "_queue_timer"):
            self._queue_timer.stop()
        for reply in tuple(self._avatar_replies):
            reply.abort()
            reply.deleteLater()
        self._avatar_replies.clear()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop(timeout_ms=2000)
        for queue_worker in tuple(self._queue_workers):
            queue_worker.stop(timeout_ms=2000)
        self._queue_workers.clear()
        self._user_access_token = ""
        self.token_edit.clear()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
