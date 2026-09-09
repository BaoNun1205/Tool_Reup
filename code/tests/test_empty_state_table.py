from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF, Theme, qconfig, setTheme

from auto_tiktok_editor.tiktok_profiles.qt_ui.components.empty_state_table import (
    EmptyStateTableWidget,
)


class EmptyStateTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_message_is_visible_only_when_the_table_has_no_rows(self):
        table = EmptyStateTableWidget(empty_text="Chưa có sản phẩm.")
        table.resize(500, 260)
        table.show()
        self.app.processEvents()

        self.assertTrue(table._empty_state_label.isVisible())
        self.assertTrue(table._empty_state_icon.isVisible())
        self.assertGreaterEqual(table._empty_state_label.height(), 52)
        self.assertGreater(table._empty_state_label.width(), 300)
        self.assertEqual(table.emptyStateText(), "Chưa có sản phẩm.")

        table.setRowCount(1)
        self.app.processEvents()
        self.assertFalse(table._empty_state_label.isVisible())

        table.setRowCount(0)
        table.setEmptyStateText("Không có dữ liệu phù hợp với bộ lọc.")
        self.app.processEvents()
        self.assertTrue(table._empty_state_label.isVisible())
        self.assertEqual(
            table.emptyStateText(),
            "Không có dữ liệu phù hợp với bộ lọc.",
        )
        table.close()

    def test_dark_mode_uses_readable_text_and_a_context_icon(self):
        original_theme = qconfig.theme
        try:
            setTheme(Theme.DARK)
            table = EmptyStateTableWidget(
                empty_text="Không có video.",
                empty_icon=FIF.VIDEO,
            )
            table._update_empty_state_theme()

            self.assertIn("#B5B9C7", table._empty_state_label.styleSheet())
            self.assertFalse(table._empty_state_icon.icon.isNull())
            table.close()
        finally:
            setTheme(original_theme)


if __name__ == "__main__":
    unittest.main()
