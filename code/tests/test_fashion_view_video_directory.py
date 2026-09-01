import sys
import tempfile
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


class FashionVideoDirectoryTests(unittest.TestCase):
    def test_existing_video_directory_rejects_blank_and_missing_paths(self):
        self.assertIsNone(fashion_view._existing_video_directory(""))
        self.assertIsNone(fashion_view._existing_video_directory("missing-video-directory"))

    def test_saving_a_valid_video_directory_normalizes_and_persists_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = mock.Mock()
            directory_edit = mock.Mock()
            directory_edit.text.return_value = temp_dir
            view = SimpleNamespace(
                video_directory_edit=directory_edit,
                _video_directory_settings=mock.Mock(return_value=settings),
                window=mock.Mock(return_value=None),
            )

            saved = FashionView._save_video_directory(view)

        normalized = str(Path(temp_dir).resolve())
        self.assertTrue(saved)
        directory_edit.setText.assert_called_once_with(normalized)
        settings.setValue.assert_called_once_with(fashion_view._VIDEO_DIRECTORY_KEY, normalized)
        settings.sync.assert_called_once_with()

    def test_choose_video_opens_in_the_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "video.mp4"
            view = SimpleNamespace(
                _video_dialog_directory=mock.Mock(return_value=temp_dir),
                _video_file_path=None,
                video_file_label=mock.Mock(),
                send_video_button=mock.Mock(),
            )
            with mock.patch.object(
                fashion_view.QFileDialog,
                "getOpenFileName",
                return_value=(str(video_path), "Video files"),
            ) as choose_file:
                FashionView._choose_video_file(view)

        self.assertEqual(choose_file.call_args.args[2], temp_dir)
        self.assertEqual(view._video_file_path, video_path)
        view.video_file_label.setText.assert_called_once_with("video.mp4")
        view.send_video_button.setEnabled.assert_called_once_with(True)

    def test_choose_directory_updates_the_field_and_saves_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory_edit = mock.Mock()
            view = SimpleNamespace(
                _video_dialog_directory=mock.Mock(return_value=""),
                video_directory_edit=directory_edit,
                _save_video_directory=mock.Mock(),
            )
            with mock.patch.object(
                fashion_view.QFileDialog,
                "getExistingDirectory",
                return_value=temp_dir,
            ):
                FashionView._choose_video_directory(view)

        directory_edit.setText.assert_called_once_with(temp_dir)
        view._save_video_directory.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
