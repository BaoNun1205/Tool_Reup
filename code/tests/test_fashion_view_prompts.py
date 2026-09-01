import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from auto_tiktok_editor.tiktok_profiles.qt_ui.views import fashion_view
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.fashion_view import FashionView
from auto_tiktok_editor import fashion_prompt_settings
from auto_tiktok_editor.fashion_prompts import DEFAULT_CHANGE_OUTFIT_PROMPT


class FashionPromptAutosaveTests(unittest.TestCase):
    def test_shared_change_outfit_prompt_has_the_requested_default_text(self):
        self.assertEqual(
            DEFAULT_CHANGE_OUTFIT_PROMPT,
            (
                "Promt thay đồ",
                "Cho nhân vật ảnh 1 thay đồ của ảnh 2 nhưng vẫn giữ bố cục của ảnh 1",
            ),
        )

    def test_old_shared_prompt_name_is_displayed_with_the_new_name(self):
        settings = mock.Mock()
        settings.value.side_effect = [
            "Thay đồ",
            "Cho nhân vật ảnh 1 thay đồ của ảnh 2 nhưng vẫn giữ bố cục của ảnh 1",
        ]

        with mock.patch.object(fashion_prompt_settings, "QSettings", return_value=settings):
            prompt = fashion_prompt_settings.load_change_outfit_prompt()

        self.assertEqual(prompt, DEFAULT_CHANGE_OUTFIT_PROMPT)

    def test_editing_the_shared_prompt_saves_and_refreshes_it(self):
        dialog = SimpleNamespace(
            exec=mock.Mock(return_value=True),
            result_data=("Updated name", "Updated content"),
        )
        view = SimpleNamespace(
            _change_outfit_prompt=DEFAULT_CHANGE_OUTFIT_PROMPT,
            window=mock.Mock(return_value=None),
            _refresh_change_outfit_prompt_card=mock.Mock(),
        )

        with mock.patch.object(
            fashion_view,
            "PromptEditorDialog",
            return_value=dialog,
        ) as editor_dialog, mock.patch.object(
            fashion_view,
            "save_change_outfit_prompt",
        ) as save_prompt:
            FashionView._edit_change_outfit_prompt(view)

        self.assertEqual(view._change_outfit_prompt, dialog.result_data)
        editor_dialog.assert_called_once_with(
            view,
            DEFAULT_CHANGE_OUTFIT_PROMPT[0],
            DEFAULT_CHANGE_OUTFIT_PROMPT[1],
            content_only=True,
        )
        save_prompt.assert_called_once_with(*dialog.result_data)
        view._refresh_change_outfit_prompt_card.assert_called_once_with()

    def test_editing_a_prompt_persists_the_updated_list_immediately(self):
        card = object()
        dialog = SimpleNamespace(exec=mock.Mock(return_value=True), result_data=("New name", "New content"))
        view = SimpleNamespace(
            _garment_prompt_widgets=[(card, "Old name", "Old content")],
            window=mock.Mock(return_value=None),
            _rebuild_garment_prompt_cards=mock.Mock(),
            _persist_current_garment_prompts=mock.Mock(),
        )

        with mock.patch.object(fashion_view, "PromptEditorDialog", return_value=dialog):
            FashionView._edit_garment_prompt(view, card)

        self.assertEqual(
            view._garment_prompt_widgets,
            [(card, "New name", "New content")],
        )
        view._rebuild_garment_prompt_cards.assert_called_once_with()
        view._persist_current_garment_prompts.assert_called_once_with()

    def test_adding_a_prompt_persists_the_updated_list_immediately(self):
        dialog = SimpleNamespace(exec=mock.Mock(return_value=True), result_data=("Name", "Content"))
        view = SimpleNamespace(
            window=mock.Mock(return_value=None),
            _append_garment_prompt=mock.Mock(),
            _persist_current_garment_prompts=mock.Mock(),
        )

        with mock.patch.object(fashion_view, "PromptEditorDialog", return_value=dialog):
            FashionView._on_add_garment_prompt(view)

        view._append_garment_prompt.assert_called_once_with("Name", "Content")
        view._persist_current_garment_prompts.assert_called_once_with()

    def test_persist_writes_the_current_prompt_list_for_the_selected_type(self):
        preset = object()
        prompts = [("Name", "Content")]
        view = SimpleNamespace(
            _current_garment_preset=mock.Mock(return_value=preset),
            _collect_garment_prompts=mock.Mock(return_value=prompts),
        )

        with mock.patch.object(fashion_view, "save_garment_prompts") as save_prompts:
            FashionView._persist_current_garment_prompts(view)

        save_prompts.assert_called_once_with(preset, prompts)

    def test_deleting_a_prompt_persists_the_updated_list_immediately(self):
        card = mock.Mock()
        remaining_card = mock.Mock()
        layout = mock.Mock()
        view = SimpleNamespace(
            _garment_prompt_widgets=[
                (card, "Delete", "Delete content"),
                (remaining_card, "Keep", "Keep content"),
            ],
            garment_prompts_layout=layout,
            _rebuild_garment_prompt_cards=mock.Mock(),
            _persist_current_garment_prompts=mock.Mock(),
        )

        FashionView._remove_garment_prompt(view, card)

        self.assertEqual(
            view._garment_prompt_widgets,
            [(remaining_card, "Keep", "Keep content")],
        )
        layout.removeWidget.assert_called_once_with(card)
        card.deleteLater.assert_called_once_with()
        view._rebuild_garment_prompt_cards.assert_called_once_with()
        view._persist_current_garment_prompts.assert_called_once_with()

    def test_an_explicitly_saved_empty_list_does_not_restore_old_prompts(self):
        preset = SimpleNamespace(key="test", first_scene="Old first", second_scene="Old second")
        settings = mock.Mock()
        settings.value.return_value = "[]"

        with mock.patch.object(fashion_prompt_settings, "QSettings", return_value=settings):
            prompts = fashion_prompt_settings.load_garment_prompts(preset)

        self.assertEqual(prompts, [])


if __name__ == "__main__":
    unittest.main()
