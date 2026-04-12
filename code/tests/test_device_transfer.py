import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tempfile
import unittest

from auto_tiktok_editor.app.device_transfer import AndroidDeviceTransfer
from auto_tiktok_editor.config import PipelineConfig


class RecordingRunner(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def run(self, args, cwd=None, check=True, capture_output=True):
        self.commands.append(list(args))
        if self.responses:
            return self.responses.pop(0)
        raise AssertionError("No response configured for command: %s" % args)


class CompletedProcessStub(object):
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class AndroidDeviceTransferTests(unittest.TestCase):
    def test_connects_over_wifi_and_selects_matching_serial(self):
        runner = RecordingRunner(
            [
                CompletedProcessStub(stdout="* daemon started successfully *\n"),
                CompletedProcessStub(stdout="connected to 192.168.1.20:5555\n"),
                CompletedProcessStub(stdout="List of devices attached\n192.168.1.20:5555\tdevice\n"),
            ]
        )
        transfer = AndroidDeviceTransfer(PipelineConfig(adb_bin="adb"), runner)

        result = transfer.connect("wifi", "192.168.1.20:5555")

        self.assertTrue(result["connected"])
        self.assertEqual(result["device_serial"], "192.168.1.20:5555")
        self.assertEqual(runner.commands[0], ["adb", "start-server"])
        self.assertEqual(runner.commands[1], ["adb", "connect", "192.168.1.20:5555"])
        self.assertEqual(runner.commands[2], ["adb", "devices"])

    def test_pushes_titles_and_videos_in_order_without_overwrite(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            titles_path = Path(temp_dir.name) / "session_titles.txt"
            titles_path.write_text("Title 1\n\nTitle 2\n", encoding="utf-8")
            video_one = Path(temp_dir.name) / "a.mp4"
            video_two = Path(temp_dir.name) / "b.mp4"
            video_one.write_text("video-a", encoding="utf-8")
            video_two.write_text("video-b", encoding="utf-8")
            runner = RecordingRunner(
                [
                    CompletedProcessStub(stdout="List of devices attached\nR58N123\tdevice\n"),
                    CompletedProcessStub(stdout=""),
                    CompletedProcessStub(stdout="1 file pushed\n"),
                    CompletedProcessStub(stdout="1 file pushed\n"),
                    CompletedProcessStub(stdout="1 file pushed\n"),
                ]
            )
            transfer = AndroidDeviceTransfer(PipelineConfig(adb_bin="adb"), runner)

            result = transfer.push_session_outputs(
                [video_one, video_two],
                titles_path=titles_path,
                session_label="session_001",
                device_serial="R58N123",
            )

            self.assertTrue(result["attempted"])
            self.assertEqual(result["pushed_count"], 2)
            self.assertEqual(result["remote_dir"], "/sdcard/Movies/AutoTikTokEditor/session_001")
            self.assertEqual(
                runner.commands[1],
                ["adb", "-s", "R58N123", "shell", "mkdir", "-p", "/sdcard/Movies/AutoTikTokEditor/session_001"],
            )
            self.assertEqual(
                runner.commands[2],
                ["adb", "-s", "R58N123", "push", str(titles_path), "/sdcard/Movies/AutoTikTokEditor/session_001/session_titles.txt"],
            )
            self.assertEqual(
                runner.commands[3],
                ["adb", "-s", "R58N123", "push", str(video_one), "/sdcard/Movies/AutoTikTokEditor/session_001/001_final_video.mp4"],
            )
            self.assertEqual(
                runner.commands[4],
                ["adb", "-s", "R58N123", "push", str(video_two), "/sdcard/Movies/AutoTikTokEditor/session_001/002_final_video.mp4"],
            )
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
