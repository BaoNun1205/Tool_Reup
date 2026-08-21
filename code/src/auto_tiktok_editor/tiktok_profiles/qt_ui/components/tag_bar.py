"""Hashtag Token Bar / Tag Input Component with individual 'x' delete buttons."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import LineEdit

from auto_tiktok_editor.tiktok_profiles.profile_manager import normalize_hashtags


class FlowLayout(QLayout):
    """A layout that arranges widgets horizontally and wraps to the next line."""

    def __init__(self, parent: QWidget | None = None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6) -> None:
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, apply_geometry: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(+left, +top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            space_x = self._h_spacing
            space_y = self._v_spacing
            item_width = item.sizeHint().width()
            item_height = item.sizeHint().height()
            next_x = x + item_width + space_x

            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item_width + space_x
                line_height = 0

            if apply_geometry:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item_height)

        return y + line_height - rect.y() + bottom


from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode


class HashtagChip(QFrame):
    """A chip badge displaying hashtag text with a '✕' remove button."""
    removed = Signal(str)

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tag = tag
        self.setObjectName("HashtagChip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(4)

        self.label = QLabel(tag, self)
        layout.addWidget(self.label)

        self.close_btn = QPushButton("✕", self)
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(lambda: self.removed.emit(self.tag))
        layout.addWidget(self.close_btn)

        self.set_theme_mode(get_current_theme_mode())

    def set_theme_mode(self, mode: str | None = None) -> None:
        m = (mode or get_current_theme_mode()).lower()
        if m == "dark":
            self.setStyleSheet("""
                QFrame#HashtagChip {
                    background-color: #27233F;
                    border: 1px solid #423A72;
                    border-radius: 6px;
                }
                QFrame#HashtagChip:hover {
                    background-color: #2F2A4C;
                    border-color: #8B7CFF;
                }
            """)
            self.label.setStyleSheet("color: #F3F4F8; font-weight: 600; font-size: 12px; background: transparent;")
            self.close_btn.setStyleSheet("""
                QPushButton {
                    color: #7F8596;
                    font-size: 11px;
                    font-weight: bold;
                    border: none;
                    background: transparent;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    color: #ffffff;
                    background-color: #F06A6A;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#HashtagChip {
                    background-color: #EEECFF;
                    border: 1px solid #D9D5FF;
                    border-radius: 6px;
                }
                QFrame#HashtagChip:hover {
                    background-color: #E5E1FF;
                    border-color: #6D5DFB;
                }
            """)
            self.label.setStyleSheet("color: #181B2A; font-weight: 600; font-size: 12px; background: transparent;")
            self.close_btn.setStyleSheet("""
                QPushButton {
                    color: #9095A5;
                    font-size: 11px;
                    font-weight: bold;
                    border: none;
                    background: transparent;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    color: #ffffff;
                    background-color: #E5484D;
                }
            """)


class YouTubeTagInput(QWidget):
    """Hashtags component displaying badges with '✕' buttons and an inline input."""
    tags_changed = Signal(str)

    def __init__(self, initial_tags: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Chips Container
        self.container = QFrame(self)
        self.container.setObjectName("ChipsContainer")
        self.flow_layout = FlowLayout(self.container, margin=2, h_spacing=6, v_spacing=6)
        main_layout.addWidget(self.container)

        # Input line
        self.input_edit = LineEdit(self)
        self.input_edit.setPlaceholderText("Gõ hashtag (vd: #xuhuong) rồi ấn Enter...")
        self.input_edit.returnPressed.connect(self._on_enter_pressed)
        self.input_edit.textChanged.connect(self._on_text_changed)
        main_layout.addWidget(self.input_edit)

        self.set_tags(initial_tags)
        self.set_theme_mode(get_current_theme_mode())

    def set_theme_mode(self, mode: str | None = None) -> None:
        m = (mode or get_current_theme_mode()).lower()
        if m == "dark":
            self.container.setStyleSheet("""
                QFrame#ChipsContainer {
                    background-color: #171A23;
                    border: 1px solid #303543;
                    border-radius: 8px;
                    padding: 8px;
                }
            """)
        else:
            self.container.setStyleSheet("""
                QFrame#ChipsContainer {
                    background-color: #FFFFFF;
                    border: 1px solid #DDE1EA;
                    border-radius: 8px;
                    padding: 8px;
                }
            """)
        for chip in self.findChildren(HashtagChip):
            chip.set_theme_mode(m)

    def _on_text_changed(self, text: str) -> None:
        if text.endswith(",") or text.endswith(" "):
            self._on_enter_pressed()

    def _on_enter_pressed(self) -> None:
        raw_text = self.input_edit.text().strip().rstrip(",")
        if not raw_text:
            return
        parts = normalize_hashtags(raw_text).split()
        for p in parts:
            clean = p if p.startswith("#") else f"#{p}"
            if clean and clean.lower() not in [t.lower() for t in self._tags]:
                self._tags.append(clean)
        self.input_edit.clear()
        self._render_chips()
        self.tags_changed.emit(self.get_tags_string())

    def _remove_tag(self, tag: str) -> None:
        self._tags = [t for t in self._tags if t.lower() != tag.lower()]
        self._render_chips()
        self.tags_changed.emit(self.get_tags_string())

    def _render_chips(self) -> None:
        while self.flow_layout.count() > 0:
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self._tags:
            empty_label = QLabel("Chưa có hashtag nào", self.container)
            empty_label.setStyleSheet("color: #71717a; font-style: italic; font-size: 11px; padding: 4px;")
            self.flow_layout.addWidget(empty_label)
        else:
            current_mode = get_current_theme_mode()
            for tag in self._tags:
                chip = HashtagChip(tag, self.container)
                chip.set_theme_mode(current_mode)
                chip.removed.connect(self._remove_tag)
                self.flow_layout.addWidget(chip)

        self.container.updateGeometry()

    def set_tags(self, tags_str: str) -> None:
        normalized = normalize_hashtags(tags_str)
        self._tags = [t for t in normalized.split() if t.startswith("#")]
        self._render_chips()

    def get_tags_string(self) -> str:
        return " ".join(self._tags)

