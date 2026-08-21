"""Account Dialog for adding / editing a TikTok Account."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    SubtitleLabel,
    BodyLabel,
    PrimaryPushButton,
    PushButton,
    InfoBar,
    InfoBarPosition,
)

from auto_tiktok_editor.tiktok_profiles.models import LOGIN_TYPES
from auto_tiktok_editor.tiktok_profiles.profile_manager import default_hashtag_for_account_name
from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import VIDEO_ROW_CUT_MODE_LABELS, VIDEO_ROW_CUT_MODE_VALUES


class AccountDialog(MessageBoxBase):
    """Fluent dialog to create or edit a TikTok Account profile."""

    def __init__(
        self,
        parent: QWidget | None = None,
        account: Any = None,
        is_edit: bool = False,
    ) -> None:
        super().__init__(parent)
        self.is_edit = is_edit
        self.account = account
        self.result_data: dict[str, Any] | None = None

        title_text = "Chỉnh sửa tài khoản TikTok" if is_edit else "Thêm tài khoản TikTok mới"
        self.titleLabel = SubtitleLabel(title_text, self)
        self.viewLayout.addWidget(self.titleLabel)

        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 16, 0, 16)
        form_layout.setSpacing(12)

        # Name
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("Tên tài khoản (ví dụ: Channel 01)")
        if account:
            self.name_edit.setText(getattr(account, "name", "") or "")
            if is_edit:
                self.name_edit.setReadOnly(True)
        form_layout.addRow(BodyLabel("Tên tài khoản:", self), self.name_edit)

        # Bot Name
        self.bot_name_edit = LineEdit(self)
        self.bot_name_edit.setPlaceholderText("Tên bot trong telegram_bots.json (tùy chọn)")
        if account:
            self.bot_name_edit.setText(getattr(account, "bot_name", "") or "")
        form_layout.addRow(BodyLabel("Tên Bot Telegram:", self), self.bot_name_edit)

        # Login Type
        self.login_type_combo = ComboBox(self)
        self.login_type_combo.addItems(list(LOGIN_TYPES))
        if account and getattr(account, "login_type", "") in LOGIN_TYPES:
            self.login_type_combo.setCurrentText(account.login_type)
        form_layout.addRow(BodyLabel("Phương thức đăng nhập:", self), self.login_type_combo)

        # Cut Mode
        self.cut_mode_combo = ComboBox(self)
        self.cut_mode_combo.addItems(list(VIDEO_ROW_CUT_MODE_LABELS.values()))
        current_cut_mode = getattr(account, "cut_mode", "original") if account else "original"
        self.cut_mode_combo.setCurrentText(VIDEO_ROW_CUT_MODE_LABELS.get(current_cut_mode, "Giữ nguyên video gốc"))
        form_layout.addRow(BodyLabel("Chế độ cắt video:", self), self.cut_mode_combo)

        # Hashtags
        self.hashtags_edit = LineEdit(self)
        self.hashtags_edit.setPlaceholderText("#tag1 #tag2 (để trống sẽ tạo theo tên profile)")
        if account:
            self.hashtags_edit.setText(getattr(account, "hashtags", "") or "")
        form_layout.addRow(BodyLabel("Hashtags mặc định:", self), self.hashtags_edit)

        # Notes
        self.note_edit = PlainTextEdit(self)
        self.note_edit.setPlaceholderText("Ghi chú, link sản phẩm affiliate (Product link: https://...)")
        self.note_edit.setFixedHeight(70)
        if account:
            self.note_edit.setPlainText(getattr(account, "note", "") or "")
        form_layout.addRow(BodyLabel("Ghi chú:", self), self.note_edit)

        self.viewLayout.addLayout(form_layout)

        # Change default buttons text
        self.yesButton.setText("Lưu" if is_edit else "Thêm")
        self.cancelButton.setText("Hủy")
        self.widget.setMinimumWidth(480)

    def validate(self) -> bool:
        name = self.name_edit.text().strip()
        if not name:
            InfoBar.warning(
                title="Thiếu thông tin",
                content="Vui lòng nhập tên tài khoản!",
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return False

        cut_mode_label = self.cut_mode_combo.currentText().strip()
        cut_mode_val = VIDEO_ROW_CUT_MODE_VALUES.get(cut_mode_label, "original")
        hashtags = self.hashtags_edit.text().strip() or default_hashtag_for_account_name(name)

        self.result_data = {
            "name": name,
            "bot_name": self.bot_name_edit.text().strip(),
            "login_type": self.login_type_combo.currentText(),
            "cut_mode": cut_mode_val,
            "hashtags": hashtags,
            "note": self.note_edit.toPlainText().strip(),
        }
        return True

    @property
    def result(self) -> dict[str, Any]:
        """Return the validated form data for callers of the dialog."""
        return self.result_data or {}
