import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from types import SimpleNamespace
import unittest
from unittest import mock

from auto_tiktok_editor import cli
from auto_tiktok_editor.phone_control import PhoneController
from auto_tiktok_editor.tiktok_profiles.qt_ui.app import TikTokProfileManagerApp
from auto_tiktok_editor.tiktok_profiles.qt_ui.views.telegram_view import TelegramView
from auto_tiktok_editor.utils import processes
from auto_tiktok_editor.utils.single_instance import _is_profile_manager_title


class ProcessCleanupTests(unittest.TestCase):
    def test_terminate_process_tree_uses_taskkill_tree_on_windows(self):
        process = mock.Mock(pid=321)
        process.poll.return_value = None

        with mock.patch.object(processes.os, "name", "nt"), mock.patch.object(
            processes, "_taskkill_tree", return_value=True
        ) as taskkill:
            stopped = processes.terminate_process_tree(process)

        self.assertTrue(stopped)
        taskkill.assert_called_once_with(321, timeout=3.0)
        process.wait.assert_called_once_with(timeout=3.0)
        process.terminate.assert_not_called()

    def test_child_tree_cleanup_only_targets_direct_children(self):
        parent_map = {10: 1, 11: 10, 12: 11, 13: 10, 99: 50}
        self.assertEqual(processes._direct_child_roots(10, parent_map), [11, 13])


class TelegramSingletonTests(unittest.TestCase):
    def test_duplicate_telegram_runtime_is_refused(self):
        guard = mock.Mock()
        guard.acquire.return_value = False
        run_callable = mock.Mock()
        with mock.patch.object(cli, "telegram_runtime_guard", return_value=guard):
            exit_code = cli._run_exclusive_telegram_runtime(run_callable)

        self.assertEqual(exit_code, 3)
        run_callable.assert_not_called()
        guard.release.assert_not_called()

    def test_telegram_runtime_always_releases_singleton(self):
        guard = mock.Mock()
        guard.acquire.return_value = True
        with mock.patch.object(cli, "telegram_runtime_guard", return_value=guard):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                cli._run_exclusive_telegram_runtime(
                    mock.Mock(side_effect=RuntimeError("boom"))
                )

        guard.release.assert_called_once_with()

    def test_second_profile_manager_click_focuses_existing_window(self):
        guard = mock.Mock()
        guard.acquire.return_value = False
        config = mock.Mock()
        with mock.patch.object(cli, "profile_manager_runtime_guard", return_value=guard), mock.patch.object(
            cli, "activate_existing_profile_manager_window", return_value=True
        ) as activate:
            exit_code = cli._run_exclusive_profile_manager(config)

        self.assertEqual(exit_code, 0)
        activate.assert_called_once_with(wait_seconds=2.0)
        guard.release.assert_not_called()

    def test_profile_manager_window_titles_include_loading_and_ready_states(self):
        self.assertTrue(_is_profile_manager_title("TikTok Profile Manager Pro"))
        self.assertTrue(_is_profile_manager_title("TikTok Profile Manager - Đang khởi động"))
        self.assertFalse(_is_profile_manager_title("Another application"))


class AppShutdownTests(unittest.TestCase):
    def test_shutdown_continues_after_a_cleanup_error_and_is_idempotent(self):
        telegram = mock.Mock()
        telegram.shutdown.side_effect = RuntimeError("telegram cleanup failed")
        fake_app = SimpleNamespace(
            _shutdown_started=False,
            telegram_view=telegram,
            phone_view=mock.Mock(),
            videos_view=mock.Mock(),
            fashion_view=mock.Mock(),
            dashboard_view=mock.Mock(),
            logs_view=mock.Mock(),
            browser_worker=mock.Mock(),
        )

        with mock.patch(
            "auto_tiktok_editor.tiktok_profiles.qt_ui.app.terminate_child_process_trees",
            return_value=2,
        ) as terminate_children:
            TikTokProfileManagerApp.shutdown(fake_app)
            TikTokProfileManagerApp.shutdown(fake_app)

        telegram.shutdown.assert_called_once_with()
        fake_app.phone_view.shutdown.assert_called_once_with()
        fake_app.browser_worker.stop.assert_called_once_with()
        terminate_children.assert_called_once_with()

    def test_phone_cleanup_closes_disconnects_and_stops_adb_server(self):
        fake_controller = SimpleNamespace(
            close=mock.Mock(),
            disconnect=mock.Mock(),
            runner=mock.Mock(),
            config=SimpleNamespace(adb_bin="adb"),
        )

        PhoneController.cleanup(fake_controller)

        fake_controller.close.assert_called_once_with()
        fake_controller.disconnect.assert_called_once_with()
        fake_controller.runner.run.assert_called_once_with(["adb", "kill-server"], check=False)

    def test_telegram_shutdown_terminates_the_owned_process_tree(self):
        process = mock.Mock()
        process.stdout = mock.Mock()
        fake_view = SimpleNamespace(
            monitor_thread=None,
            bot_process=process,
            status_card=mock.Mock(),
        )

        with mock.patch(
            "auto_tiktok_editor.tiktok_profiles.qt_ui.views.telegram_view.terminate_process_tree"
        ) as terminate_tree:
            TelegramView._on_stop_bot(fake_view, show_status=False)

        terminate_tree.assert_called_once_with(process, timeout=3)
        process.stdout.close.assert_called_once_with()
        self.assertIsNone(fake_view.bot_process)


if __name__ == "__main__":
    unittest.main()
