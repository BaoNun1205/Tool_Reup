"""Table widget that explains an empty data area instead of leaving it blank."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon as FIF, IconWidget, TableWidget, isDarkTheme


class EmptyStateTableWidget(TableWidget):
    """A Fluent table with a centred message whenever it has no rows."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        empty_text: str = "Chưa có dữ liệu để hiển thị.",
        empty_icon=FIF.INFO,
    ) -> None:
        super().__init__(parent)
        self._empty_state_icon_source = empty_icon
        self._empty_state_widget = QWidget(self.viewport())
        self._empty_state_widget.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        empty_layout = QVBoxLayout(self._empty_state_widget)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)

        self._empty_state_icon = IconWidget(self._empty_state_widget)
        self._empty_state_icon.setFixedSize(42, 42)
        empty_layout.addWidget(
            self._empty_state_icon,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        self._empty_state_label = QLabel(
            str(empty_text or "").strip(),
            self._empty_state_widget,
        )
        self._empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state_label.setWordWrap(True)
        self._empty_state_label.setMinimumHeight(52)
        self._empty_state_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._empty_state_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        empty_layout.addWidget(self._empty_state_label)
        empty_layout.addStretch(1)
        self._sync_empty_state()

    def setEmptyStateText(self, text: str) -> None:
        self._empty_state_label.setText(str(text or "").strip())
        self._sync_empty_state()

    def emptyStateText(self) -> str:
        return self._empty_state_label.text()

    def setEmptyStateIcon(self, icon) -> None:
        self._empty_state_icon_source = icon
        self._update_empty_state_theme()

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        if hasattr(self, "_empty_state_label"):
            self._sync_empty_state()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_empty_state_label"):
            self._layout_empty_state()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_empty_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if hasattr(self, "_empty_state_label") and event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        ):
            self._update_empty_state_theme()

    def _layout_empty_state(self) -> None:
        self._empty_state_widget.setGeometry(self.viewport().rect())

    def _update_empty_state_theme(self) -> None:
        dark = isDarkTheme()
        text_color = "#B5B9C7" if dark else "#667085"
        icon_color = QColor("#9B8CFF" if dark else "#6D5DFB")
        self._empty_state_label.setStyleSheet(
            "background: transparent; border: none; color: %s;"
            " font-size: 14px; font-weight: 500;" % text_color
        )
        icon_source = self._empty_state_icon_source
        if hasattr(icon_source, "icon"):
            self._empty_state_icon.setIcon(icon_source.icon(color=icon_color))
        else:
            self._empty_state_icon.setIcon(icon_source)

    def _sync_empty_state(self) -> None:
        self._layout_empty_state()
        self._update_empty_state_theme()
        should_show = self.rowCount() == 0 and bool(self._empty_state_label.text())
        self._empty_state_widget.setVisible(should_show)
        if should_show:
            self._empty_state_widget.raise_()
