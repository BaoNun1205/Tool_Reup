import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from auto_tiktok_editor.tiktok_profiles.qt_ui.views.fashion_view import FashionView


def _product(*, image_path: str = "image.jpg") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        created_at=datetime(2026, 1, 1, 8, 30),
        product_name="Product",
        description="Description",
        product_url="https://example.com/product",
        status="ready",
        product_id="product-id",
        image_path=image_path,
        video_path="",
        updated_at=datetime(2026, 1, 1, 8, 30),
    )


class FashionProductRefreshTests(unittest.TestCase):
    def _view(self, existing_product, refreshed_product):
        existing_item = mock.Mock()
        existing_item.data.return_value = existing_product
        image_widget = object()
        table = mock.Mock()
        table.rowCount.return_value = 1
        table.item.return_value = existing_item
        table.cellWidget.return_value = image_widget
        action_widget = object()
        view = SimpleNamespace(
            manager=SimpleNamespace(list_fashion_products=lambda: [refreshed_product]),
            products_table=table,
            _fashion_products_signature=None,
            _fashion_job_ids_in_progress=set(),
            _fashion_image_widget=mock.Mock(return_value=object()),
            _fashion_action_widget=mock.Mock(return_value=action_widget),
            _can_reuse_fashion_image_widget=FashionView._can_reuse_fashion_image_widget,
        )
        return view, table, action_widget

    def test_refresh_keeps_the_existing_image_widget_when_the_image_is_unchanged(self):
        view, table, action_widget = self._view(_product(), _product())

        FashionView.refresh_fashion_products(view, force=True)

        view._fashion_image_widget.assert_not_called()
        self.assertNotIn(
            mock.call(0, 0, mock.ANY),
            table.setCellWidget.call_args_list,
        )
        table.setCellWidget.assert_any_call(0, 6, action_widget)

    def test_refresh_replaces_the_image_widget_when_its_path_changes(self):
        view, table, _action_widget = self._view(
            _product(image_path="old.jpg"),
            _product(image_path="new.jpg"),
        )

        FashionView.refresh_fashion_products(view, force=True)

        view._fashion_image_widget.assert_called_once_with(view.manager.list_fashion_products()[0])
        table.setCellWidget.assert_any_call(0, 0, view._fashion_image_widget.return_value)

    def test_reuse_requires_the_same_product_and_image_path(self):
        widget = object()
        existing = _product()
        refreshed = _product()

        self.assertTrue(
            FashionView._can_reuse_fashion_image_widget((existing, widget), refreshed)
        )
        refreshed.id = 2
        self.assertFalse(
            FashionView._can_reuse_fashion_image_widget((existing, widget), refreshed)
        )


if __name__ == "__main__":
    unittest.main()
