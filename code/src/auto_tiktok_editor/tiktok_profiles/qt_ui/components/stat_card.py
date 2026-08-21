"""Modern Metric / Stat Card Component for Fluent Design."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CardWidget, IconWidget, SubtitleLabel, CaptionLabel, TitleLabel


from auto_tiktok_editor.tiktok_profiles.qt_ui.theme import get_current_theme_mode


class StatCard(CardWidget):
    """A stylish KPI/Metric card with icon, title, value, and description."""

    def __init__(
        self,
        icon: Any = None,
        title: str = "Metric",
        value: str = "0",
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(96)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        if icon:
            self.icon_widget = IconWidget(icon, self)
            self.icon_widget.setFixedSize(36, 36)
            layout.addWidget(self.icon_widget, alignment=Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.title_label = CaptionLabel(title, self)
        text_layout.addWidget(self.title_label)

        self.value_label = TitleLabel(value, self)
        font = self.value_label.font()
        font.setPointSize(16)
        font.setBold(True)
        self.value_label.setFont(font)
        text_layout.addWidget(self.value_label)

        if description:
            self.desc_label = CaptionLabel(description, self)
            text_layout.addWidget(self.desc_label)
        else:
            self.desc_label = None

        layout.addLayout(text_layout)
        layout.addStretch(1)

        self.set_theme_mode(get_current_theme_mode())

    def set_theme_mode(self, mode: str | None = None) -> None:
        m = (mode or get_current_theme_mode()).lower()
        if m == "dark":
            self.setStyleSheet("""
                CardWidget {
                    background-color: #1A1D27;
                    border: 1px solid #2A2F3D;
                    border-radius: 10px;
                }
                CardWidget:hover {
                    border-color: #423A72;
                    background-color: #202431;
                }
            """)
            self.title_label.setTextColor("#B5B9C7", "#B5B9C7")
            self.value_label.setTextColor("#F3F4F8", "#F3F4F8")
            if self.desc_label:
                self.desc_label.setTextColor("#7F8596", "#7F8596")
        else:
            self.setStyleSheet("""
                CardWidget {
                    background-color: #FFFFFF;
                    border: 1px solid #E4E7EF;
                    border-radius: 10px;
                }
                CardWidget:hover {
                    border-color: #D9D5FF;
                    background-color: #FAFAFF;
                }
            """)
            self.title_label.setTextColor("#5F6475", "#5F6475")
            self.value_label.setTextColor("#181B2A", "#181B2A")
            if self.desc_label:
                self.desc_label.setTextColor("#9095A5", "#9095A5")

    def set_value(self, value: str) -> None:
        self.value_label.setText(str(value))

    def set_description(self, description: str) -> None:
        if self.desc_label:
            self.desc_label.setText(str(description))
