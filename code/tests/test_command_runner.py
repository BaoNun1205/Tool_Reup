import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest
from unittest import mock

from auto_tiktok_editor.utils.command import CommandRunner


class CommandRunnerTests(unittest.TestCase):
    @mock.patch("auto_tiktok_editor.utils.command.shutil.which", return_value=r"C:\tools\ffmpeg.exe")
    @mock.patch("auto_tiktok_editor.utils.command.subprocess.run")
    def test_run_hides_windows_console_for_external_tools(self, run_mock, _which_mock):
        runner = CommandRunner()
        run_mock.return_value.returncode = 0
        run_mock.return_value.stderr = ""

        with mock.patch("auto_tiktok_editor.utils.command.os.name", "nt"), mock.patch(
            "auto_tiktok_editor.utils.command.subprocess.STARTUPINFO"
        ) as startupinfo_cls, mock.patch(
            "auto_tiktok_editor.utils.command.subprocess.STARTF_USESHOWWINDOW", 1
        ), mock.patch(
            "auto_tiktok_editor.utils.command.subprocess.SW_HIDE", 0
        ), mock.patch(
            "auto_tiktok_editor.utils.command.subprocess.CREATE_NO_WINDOW", 134217728
        ):
            startupinfo = mock.Mock()
            startupinfo.dwFlags = 0
            startupinfo_cls.return_value = startupinfo

            runner.run(["ffmpeg", "-version"])

        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 134217728)
        self.assertIs(kwargs["startupinfo"], startupinfo)
        self.assertEqual(startupinfo.dwFlags, 1)
        self.assertEqual(startupinfo.wShowWindow, 0)

    @mock.patch("auto_tiktok_editor.utils.command.shutil.which", return_value="/usr/bin/ffmpeg")
    @mock.patch("auto_tiktok_editor.utils.command.subprocess.run")
    def test_run_omits_windows_only_flags_on_non_windows(self, run_mock, _which_mock):
        runner = CommandRunner()
        run_mock.return_value.returncode = 0
        run_mock.return_value.stderr = ""

        with mock.patch.object(runner, "_windows_subprocess_kwargs", return_value={}):
            runner.run(["ffmpeg", "-version"])

        kwargs = run_mock.call_args.kwargs
        self.assertNotIn("creationflags", kwargs)
        self.assertNotIn("startupinfo", kwargs)


if __name__ == "__main__":
    unittest.main()
