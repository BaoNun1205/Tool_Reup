"""Responsive combo box variants for frequently used toolbar filters."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction

from qfluentwidgets import ComboBox
from qfluentwidgets.components.widgets.menu import MenuAnimationType


class InstantComboBox(ComboBox):
    """A Fluent ComboBox that opens its item menu immediately, without animation."""

    popupOpened = Signal()
    popupClosed = Signal()

    def _showComboMenu(self) -> None:
        if not self.items:
            return

        menu = self._createComboMenu()
        for item in self.items:
            action = QAction(item.icon, item.text)
            action.setEnabled(item.isEnabled)
            menu.addAction(action)

        menu.view.itemClicked.connect(
            lambda item: self._onItemClicked(self.findText(item.text().lstrip()))
        )
        if menu.view.width() < self.width():
            menu.view.setMinimumWidth(self.width())
            menu.adjustSize()

        menu.setMaxVisibleItems(self.maxVisibleItems())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def _on_closed() -> None:
            self._onDropMenuClosed()
            self.popupClosed.emit()

        menu.closedSignal.connect(_on_closed)
        self.dropMenu = menu

        if self.currentIndex() >= 0:
            menu.setDefaultAction(menu.actions()[self.currentIndex()])

        x = -menu.width() // 2 + menu.layout().contentsMargins().left() + self.width() // 2
        down_pos = self.mapToGlobal(QPoint(x, self.height()))
        up_pos = self.mapToGlobal(QPoint(x, 0))
        down_height = menu.view.heightForAnimation(down_pos, MenuAnimationType.DROP_DOWN)
        up_height = menu.view.heightForAnimation(up_pos, MenuAnimationType.PULL_UP)

        if down_height >= up_height:
            menu.view.adjustSize(down_pos, MenuAnimationType.DROP_DOWN)
            menu.move(down_pos)
        else:
            menu.view.adjustSize(up_pos, MenuAnimationType.PULL_UP)
            menu.move(up_pos)

        self.popupOpened.emit()
        menu.show()
