from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _Signal:
    def connect(self, callback):
        pass


class _WorkerThread:
    instances = []

    def __init__(self, function, parent=None):
        self.function = function
        self.parent = parent
        self.finished_task = _Signal()
        self.error_task = _Signal()
        self.started = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True


class VideosViewStaleStatusTests(unittest.TestCase):
    def test_send_reloads_ready_status_held_by_an_in_place_updated_row(self):
        from auto_tiktok_editor import phone_control
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views import videos_view

        stale_video = SimpleNamespace(id=3235, status="queued")
        current_video = SimpleNamespace(id=3235, status="ready")
        manager = SimpleNamespace(get_video=mock.Mock(return_value=current_video))
        view = SimpleNamespace(
            manager=manager,
            config=SimpleNamespace(),
            _sending_video_ids=set(),
            _update_video_action_state=mock.Mock(),
            _selected_video=None,
            _active_workers=[],
            window=lambda: None,
        )
        _WorkerThread.instances.clear()

        settings = SimpleNamespace(address="", connection_mode="usb")
        with mock.patch.object(phone_control, "load_phone_control_settings", return_value=settings), mock.patch.object(
            phone_control, "PhoneController", return_value=mock.Mock()
        ), mock.patch.object(videos_view, "WorkerThread", _WorkerThread), mock.patch.object(
            videos_view.InfoBar, "warning"
        ) as warning, mock.patch.object(videos_view.InfoBar, "info"):
            videos_view.VideosView._on_send_video(view, stale_video)

        manager.get_video.assert_called_once_with(3235)
        warning.assert_not_called()
        self.assertEqual(view._sending_video_ids, {3235})
        self.assertEqual(len(_WorkerThread.instances), 1)
        self.assertTrue(_WorkerThread.instances[0].started)


if __name__ == "__main__":
    unittest.main()
