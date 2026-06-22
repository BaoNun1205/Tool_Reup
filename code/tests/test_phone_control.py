import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.phone_control import (
    DEFAULT_PUSH_TARGET,
    PhoneController,
    PhoneControlSettings,
    TIKTOK_UPLOAD_DEEPLINKS,
    load_phone_control_settings,
    normalize_phone_address,
    normalize_monitor_target,
    normalize_scrcpy_max_fps,
    normalize_scrcpy_max_size,
    normalize_scrcpy_video_bit_rate,
    save_phone_control_settings,
)


class RunnerStub:
    def __init__(self, responses=None):
        self.tools = []
        self.commands = []
        self.responses = list(responses or [])

    def ensure_tool(self, tool):
        self.tools.append(tool)

    def run(self, args, cwd=None, check=True, capture_output=True):
        self.commands.append(list(args))
        if self.responses:
            return self.responses.pop(0)
        return CompletedProcessStub()


class CompletedProcessStub:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class DeviceTransferStub:
    def __init__(self, connected=True):
        self.connected = connected
        self.calls = []

    def connect(self, mode, address):
        self.calls.append((mode, address))
        return {
            "connected": self.connected,
            "message": "connected" if self.connected else "not connected",
        }


class ProcessStub:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class PhoneControlTests(unittest.TestCase):
    def test_normalize_phone_address_adds_default_port(self):
        self.assertEqual(normalize_phone_address("192.168.1.20"), "192.168.1.20:5555")
        self.assertEqual(normalize_phone_address("192.168.1.20:37123"), "192.168.1.20:37123")

    def test_normalize_phone_address_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            normalize_phone_address("not-an-ip")
        with self.assertRaises(ValueError):
            normalize_phone_address("192.168.1.20:99999")

    def test_normalize_monitor_target_defaults_to_primary(self):
        self.assertEqual(normalize_monitor_target("secondary"), "secondary")
        self.assertEqual(normalize_monitor_target("unknown"), "primary")

    def test_normalize_scrcpy_quality_defaults_to_balanced_profile(self):
        self.assertEqual(normalize_scrcpy_max_size("invalid"), 1280)
        self.assertEqual(normalize_scrcpy_max_fps(120), 60)
        self.assertEqual(normalize_scrcpy_video_bit_rate("20M"), "6M")

    def test_settings_roundtrip(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            settings_path = Path(temp_dir.name) / "phone_control_settings.json"
            with mock.patch("auto_tiktok_editor.phone_control._settings_path", return_value=settings_path):
                save_phone_control_settings(
                    PhoneControlSettings(
                        address="192.168.1.20:5555",
                        keep_screen_awake=True,
                        turn_screen_off=True,
                        always_on_top=True,
                        dock_position="right",
                        monitor_target="secondary",
                        max_size=1600,
                        max_fps=30,
                        video_bit_rate="8M",
                    )
                )
                loaded = load_phone_control_settings()
            self.assertEqual(loaded.address, "192.168.1.20:5555")
            self.assertTrue(loaded.keep_screen_awake)
            self.assertTrue(loaded.turn_screen_off)
            self.assertTrue(loaded.always_on_top)
            self.assertEqual(loaded.dock_position, "right")
            self.assertEqual(loaded.monitor_target, "secondary")
            self.assertEqual(loaded.max_size, 1600)
            self.assertEqual(loaded.max_fps, 30)
            self.assertEqual(loaded.video_bit_rate, "8M")
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["address"], loaded.address)
        finally:
            temp_dir.cleanup()

    def test_connect_and_open_launches_scrcpy_for_connected_device(self):
        runner = RunnerStub()
        device_transfer = DeviceTransferStub()
        process = ProcessStub()
        events = []
        config = PipelineConfig(adb_bin="C:/adb/adb.exe", scrcpy_bin="D:/scrcpy/scrcpy.exe")
        controller = PhoneController(
            config,
            runner=runner,
            device_transfer=device_transfer,
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.subprocess.Popen", return_value=process) as popen, mock.patch.object(
            controller,
            "_start_media_watcher",
        ), mock.patch.object(controller, "_start_clipboard_helper"), mock.patch.object(
            controller,
            "_start_scrcpy_monitor",
        ), mock.patch.object(controller, "_start_window_dock") as start_window_dock:
            result = controller.connect_and_open(
                "192.168.1.20",
                keep_screen_awake=True,
                turn_screen_off=True,
                always_on_top=True,
                dock_position="right",
                monitor_target="secondary",
                max_size=1280,
                max_fps=60,
                video_bit_rate="6M",
            )

        self.assertEqual(device_transfer.calls, [("wifi", "192.168.1.20:5555")])
        self.assertEqual(runner.tools, ["C:/adb/adb.exe", "D:/scrcpy/scrcpy.exe"])
        self.assertEqual(result["address"], "192.168.1.20:5555")
        self.assertEqual(
            popen.call_args.args[0][:3],
            ["D:/scrcpy/scrcpy.exe", "--serial", "192.168.1.20:5555"],
        )
        self.assertEqual(
            popen.call_args.args[0][-12:],
            [
                "--push-target",
                "/sdcard/DCIM/Camera/",
                "--max-size",
                "1280",
                "--max-fps",
                "60",
                "--video-bit-rate",
                "6M",
                "--shortcut-mod=rctrl",
                "--stay-awake",
                "--turn-screen-off",
                "--always-on-top",
            ],
        )
        self.assertEqual(
            popen.call_args.kwargs["env"]["ADB"],
            str(Path("C:/adb/adb.exe").resolve()),
        )
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
        start_window_dock.assert_called_once_with(
            process,
            "right",
            "secondary",
            True,
        )

        controller.close()
        self.assertTrue(process.terminated)
        self.assertEqual(
            [event["action"] for event in events],
            ["phone_connected", "scrcpy_started", "scrcpy_closed"],
        )

    def test_connect_only_uses_adb_without_launching_scrcpy(self):
        runner = RunnerStub()
        device_transfer = DeviceTransferStub()
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=device_transfer,
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.subprocess.Popen") as popen:
            result = controller.connect("192.168.1.20")

        self.assertEqual(result["address"], "192.168.1.20:5555")
        self.assertEqual(device_transfer.calls, [("wifi", "192.168.1.20:5555")])
        self.assertEqual(runner.tools, ["adb"])
        self.assertEqual(
            runner.commands[0],
            ["adb", "-s", "192.168.1.20:5555", "shell", "mkdir", "-p", DEFAULT_PUSH_TARGET],
        )
        self.assertTrue(controller.is_connected())
        self.assertFalse(controller.is_running())
        popen.assert_not_called()
        self.assertEqual([event["action"] for event in events], ["phone_connected"])

    def test_open_tiktok_upload_uses_installed_package_deeplink(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="package:/data/app/com.ss.android.ugc.trill/base.apk\n"),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="Physical size: 1080x2400\n"),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="Physical size: 1080x2400\n"),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="Physical size: 1080x2400\n"),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Tiếp' content-desc='' "
                        "bounds='[751,2298][841,2353]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Thêm mô tả...' class='android.widget.EditText' "
                        "bounds='[44,244][654,670]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )
        controller.connected_serial = "192.168.1.20:5555"

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.open_tiktok_upload()

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_has_calls(
            [
                mock.call(1.2),
                mock.call(1.0),
                mock.call(1.0),
                mock.call(1.0),
                mock.call(1.2),
            ]
        )
        self.assertEqual(result["deeplink"], TIKTOK_UPLOAD_DEEPLINKS[0])
        self.assertEqual(result["package_name"], "com.ss.android.ugc.trill")
        self.assertEqual(
            runner.commands[1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                TIKTOK_UPLOAD_DEEPLINKS[0],
                "-p",
                "com.ss.android.ugc.trill",
            ],
        )
        self.assertEqual(
            runner.commands[3],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "540",
                "2244",
            ],
        )
        self.assertEqual(
            runner.commands[5],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "91",
                "2191",
            ],
        )
        self.assertEqual(
            runner.commands[7],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "180",
                "528",
            ],
        )
        self.assertEqual(
            runner.commands[10],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "796",
                "2325",
            ],
        )
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "349",
                "457",
            ],
        )
        self.assertEqual(events[-1]["action"], "phone_tiktok_upload_opened")

    def test_paste_text_with_scrcpy_sets_clipboard_and_sends_shortcut(self):
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=RunnerStub(),
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )
        controller.process = ProcessStub()

        with mock.patch.object(controller, "_set_windows_clipboard_text") as set_clipboard, mock.patch.object(
            controller,
            "_send_scrcpy_paste_shortcut",
        ) as send_shortcut:
            result = controller.paste_text_with_scrcpy("Mô tả\n#hashtag")

        set_clipboard.assert_called_once_with("Mô tả\n#hashtag")
        send_shortcut.assert_called_once_with()
        self.assertTrue(result["pasted"])
        self.assertEqual(events[-1]["action"], "phone_text_pasted")

    def test_press_space_and_close_keyboard_sends_space_then_back(self):
        runner = RunnerStub()
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.press_space_and_close_keyboard("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        self.assertEqual(
            runner.commands,
            [
                ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "62"],
                ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "4"],
            ],
        )
        sleep.assert_called_once_with(0.15)
        self.assertEqual(result["address"], "192.168.1.20:5555")
        self.assertEqual(events[-1]["action"], "phone_keyboard_closed")

    def test_tap_tiktok_add_link_uses_ui_text(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='Thêm liên kết' "
                        "bounds='[0,1122][1080,1268]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_add_link("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_called_once_with(0.4)
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "540",
                "1195",
            ],
        )
        self.assertEqual(result["tap_x"], "540")
        self.assertEqual(result["tap_y"], "1195")
        self.assertEqual(events[-1]["action"], "phone_add_link_opened")

    def test_tap_tiktok_product_link_uses_ui_text(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Sản phẩm' content-desc='' "
                        "bounds='[154,2027][1036,2079]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_product_link("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_called_once_with(0.5)
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "595",
                "2053",
            ],
        )
        self.assertEqual(result["tap_x"], "595")
        self.assertEqual(result["tap_y"], "2053")
        self.assertEqual(events[-1]["action"], "phone_product_link_opened")

    def test_tap_tiktok_product_search_field_uses_product_search_text_center(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Tìm kiếm sản phẩm' content-desc='' "
                        "bounds='[60,260][1020,370]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_product_search_field("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_has_calls([mock.call(1.0), mock.call(0.4)])
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "540",
                "315",
            ],
        )
        self.assertEqual(result["tap_x"], "540")
        self.assertEqual(result["tap_y"], "315")
        self.assertEqual(result["bounds"], "[60,260][1020,370]")
        self.assertIn("T", result["text"])
        self.assertEqual(events[-1]["action"], "phone_product_search_focused")
        self.assertEqual(events[-1]["bounds"], "[60,260][1020,370]")

    def test_tap_tiktok_product_search_field_ignores_generic_search_until_product_search_text(self):
        generic_search_xml = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<hierarchy><node text='' content-desc='Tìm kiếm' bounds='[189,139][233,183]' /></hierarchy>"
        )
        search_xml = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<hierarchy><node text='Tìm kiếm sản phẩm' content-desc='' "
            "bounds='[60,260][1020,370]' /></hierarchy>"
        )
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(stdout=generic_search_xml),
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(stdout=search_xml),
                CompletedProcessStub(),
            ]
        )
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep"):
            result = controller.tap_tiktok_product_search_field("192.168.1.20:5555")

        self.assertEqual(
            runner.commands[-1],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "540", "315"],
        )
        self.assertEqual(result["tap_x"], "540")
        self.assertEqual(result["tap_y"], "315")

    def test_search_tiktok_product_id_pastes_id_and_presses_enter(self):
        product_id = "1730667245645826792"
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='1730667245645826792' content-desc='' "
                        "bounds='[100,100][600,180]' /></hierarchy>"
                    ),
                ),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )
        controller.process = ProcessStub()

        with mock.patch.object(controller, "_set_windows_clipboard_text") as set_clipboard, mock.patch.object(
            controller,
            "_send_scrcpy_paste_shortcut",
        ) as send_shortcut, mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.search_tiktok_product_id("192.168.1.20:5555", " %s " % product_id)

        self.assertEqual(runner.tools, ["adb"])
        set_clipboard.assert_called_once_with(product_id)
        send_shortcut.assert_called_once_with()
        sleep.assert_has_calls([mock.call(0.5), mock.call(1.0)])
        self.assertEqual(
            runner.commands,
            [
                ["adb", "-s", "192.168.1.20:5555", "shell", "uiautomator", "dump", "/sdcard/tiktok_tool_window.xml"],
                ["adb", "-s", "192.168.1.20:5555", "shell", "cat", "/sdcard/tiktok_tool_window.xml"],
                ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "66"],
            ],
        )
        self.assertEqual(result["product_id"], product_id)
        self.assertEqual(events[-1]["action"], "phone_product_id_searched")

    def test_search_tiktok_product_id_refocuses_when_paste_does_not_land(self):
        product_id = "1730667245645826792"
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(stdout="<hierarchy></hierarchy>"),
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Tìm kiếm sản phẩm' content-desc='' "
                        "bounds='[60,260][1020,370]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='1730667245645826792' content-desc='' "
                        "bounds='[100,100][600,180]' /></hierarchy>"
                    ),
                ),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )
        controller.process = ProcessStub()

        with mock.patch.object(controller, "_set_windows_clipboard_text") as set_clipboard, mock.patch.object(
            controller,
            "_send_scrcpy_paste_shortcut",
        ) as send_shortcut, mock.patch("auto_tiktok_editor.phone_control.time.sleep"):
            result = controller.search_tiktok_product_id("192.168.1.20:5555", product_id)

        self.assertEqual(set_clipboard.call_count, 2)
        self.assertEqual(send_shortcut.call_count, 2)
        self.assertEqual(result["product_id"], product_id)
        self.assertIn(
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "540", "315"],
            runner.commands,
        )
        self.assertEqual(runner.commands[-1], ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "66"])
        self.assertEqual(events[-1]["action"], "phone_product_id_searched")

    def test_tap_tiktok_product_add_button_uses_add_button_desc(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='button_add_product' "
                        "bounds='[728,573][1036,661]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_product_add_button("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_called_once_with(1.0)
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "882",
                "617",
            ],
        )
        self.assertEqual(result["tap_x"], "882")
        self.assertEqual(result["tap_y"], "617")
        self.assertEqual(events[-1]["action"], "phone_product_add_tapped")

    def test_tap_optional_tiktok_add_popup_uses_sparse_dialog(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='' bounds='[0,0][1080,2416]'>"
                        "<node text='' content-desc='' bounds='[0,0][1080,2416]' clickable='true' />"
                        "<node text='' content-desc='' bounds='[155,1008][925,1164]' "
                        "class='android.widget.ScrollView' focusable='true' />"
                        "</node></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_optional_tiktok_add_popup("192.168.1.20:5555")

        self.assertEqual(runner.tools, ["adb"])
        sleep.assert_called_once_with(0.6)
        self.assertEqual(
            runner.commands[-1],
            [
                "adb",
                "-s",
                "192.168.1.20:5555",
                "shell",
                "input",
                "tap",
                "755",
                "1123",
            ],
        )
        self.assertTrue(result["tapped"])
        self.assertEqual(result["source"], "dialog")
        self.assertEqual(events[-1]["action"], "phone_optional_add_popup_tapped")

    def test_tap_optional_tiktok_add_popup_skips_when_absent(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='' bounds='[0,0][1080,2416]'>"
                        "<node text='Product attached' content-desc='' bounds='[44,300][1036,360]' />"
                        "</node></hierarchy>"
                    ),
                ),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep"):
            result = controller.tap_optional_tiktok_add_popup("192.168.1.20:5555")

        self.assertEqual(len(runner.commands), 2)
        self.assertFalse(result["tapped"])
        self.assertEqual(events, [])

    def test_replace_invalid_tiktok_product_name_replaces_disabled_anchor_name(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='' bounds='[0,0][1080,2416]'>"
                        "<node text='[MUA 3 GIAM 50%] Gel Nha Dam D' "
                        "content-desc='edit_anchor_name_input' class='android.widget.EditText' "
                        "bounds='[44,914][1011,963]' />"
                        "<node text='' content-desc=' disabled,edit_anchor_add_button' "
                        "bounds='[44,2240][1036,2372]' />"
                        "</node></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )
        controller.process = ProcessStub()

        with mock.patch.object(controller, "paste_text_with_scrcpy") as paste, mock.patch(
            "auto_tiktok_editor.phone_control.time.sleep"
        ):
            result = controller.replace_invalid_tiktok_product_name("192.168.1.20:5555")

        self.assertTrue(result["replaced"])
        paste.assert_called_once_with("Mua ở đây")
        self.assertEqual(
            runner.commands[2],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "527", "938"],
        )
        self.assertEqual(
            runner.commands[3],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "123"],
        )
        self.assertEqual(runner.commands[-2], ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "67"])
        self.assertEqual(runner.commands[-1], ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "4"])
        self.assertEqual(events[-1]["action"], "phone_product_name_replaced")

    def test_replace_invalid_tiktok_product_name_skips_when_screen_absent(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='' bounds='[0,0][1080,2416]' /></hierarchy>"
                    ),
                ),
            ]
        )
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        with mock.patch.object(controller, "paste_text_with_scrcpy") as paste, mock.patch(
            "auto_tiktok_editor.phone_control.time.sleep"
        ):
            result = controller.replace_invalid_tiktok_product_name("192.168.1.20:5555")

        self.assertFalse(result["replaced"])
        paste.assert_not_called()
        self.assertEqual(
            runner.commands[-1],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "keyevent", "4"],
        )

    def test_tap_tiktok_anchor_final_add_button_uses_enabled_anchor_button(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='' content-desc='' bounds='[0,0][1080,2416]'>"
                        "<node text='' content-desc=' edit_anchor_add_button' "
                        "bounds='[44,2240][1036,2372]' />"
                        "</node></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_anchor_final_add_button("192.168.1.20:5555")

        sleep.assert_called_once_with(0.5)
        self.assertEqual(
            runner.commands[-1],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "540", "2306"],
        )
        self.assertEqual(result["tap_x"], "540")
        self.assertEqual(result["tap_y"], "2306")
        self.assertEqual(events[-1]["action"], "phone_anchor_final_add_tapped")

    def test_tap_tiktok_more_options_uses_ui_text(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Tùy chọn khác' content-desc='' "
                        "bounds='[121,1560][406,1612]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_more_options("192.168.1.20:5555")

        sleep.assert_called_once_with(0.7)
        self.assertEqual(
            runner.commands[-1],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "263", "1586"],
        )
        self.assertEqual(result["tap_x"], "263")
        self.assertEqual(result["tap_y"], "1586")
        self.assertEqual(events[-1]["action"], "phone_more_options_opened")

    def test_tap_tiktok_schedule_post_uses_ui_text(self):
        runner = RunnerStub(
            responses=[
                CompletedProcessStub(stdout="UI hierarchy dumped to: /sdcard/tiktok_tool_window.xml\n"),
                CompletedProcessStub(
                    stdout=(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                        "<hierarchy><node text='Lên lịch đăng' content-desc='' "
                        "bounds='[121,1613][384,1665]' /></hierarchy>"
                    ),
                ),
                CompletedProcessStub(),
            ]
        )
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.sleep") as sleep:
            result = controller.tap_tiktok_schedule_post("192.168.1.20:5555")

        sleep.assert_called_once_with(0.7)
        self.assertEqual(
            runner.commands[-1],
            ["adb", "-s", "192.168.1.20:5555", "shell", "input", "tap", "252", "1639"],
        )
        self.assertEqual(result["tap_x"], "252")
        self.assertEqual(result["tap_y"], "1639")
        self.assertEqual(events[-1]["action"], "phone_schedule_post_opened")

    def test_capture_screenshot_saves_png_and_emits_event(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            runner = RunnerStub()
            events = []
            controller = PhoneController(
                PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
                runner=runner,
                device_transfer=DeviceTransferStub(),
                on_event=events.append,
            )

            def write_png(*_args, **kwargs):
                kwargs["stdout"].write(b"\x89PNG\r\n\x1a\nimage-data")
                return CompletedProcessStub()

            with mock.patch(
                "auto_tiktok_editor.phone_control.subprocess.run",
                side_effect=write_png,
            ) as run:
                result = controller.capture_screenshot(
                    "192.168.1.20",
                    Path(temp_dir.name),
                )

            screenshot_path = Path(result["path"])
            self.assertTrue(screenshot_path.exists())
            self.assertEqual(screenshot_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(runner.tools, ["adb"])
            self.assertEqual(
                run.call_args.args[0],
                [
                    "adb",
                    "-s",
                    "192.168.1.20:5555",
                    "exec-out",
                    "screencap",
                    "-p",
                ],
            )
            self.assertEqual(events[0]["action"], "phone_screenshot_saved")
        finally:
            temp_dir.cleanup()

    def test_capture_screenshot_can_copy_image_to_clipboard(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            controller = PhoneController(
                PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
                runner=RunnerStub(),
                device_transfer=DeviceTransferStub(),
            )

            def write_png(*_args, **kwargs):
                kwargs["stdout"].write(b"\x89PNG\r\n\x1a\nimage-data")
                return CompletedProcessStub()

            with mock.patch(
                "auto_tiktok_editor.phone_control.subprocess.run",
                side_effect=write_png,
            ), mock.patch.object(controller, "_copy_image_to_clipboard") as copy:
                result = controller.capture_screenshot(
                    "192.168.1.20",
                    Path(temp_dir.name),
                    copy_to_clipboard=True,
                )

            copy.assert_called_once_with(Path(result["path"]))
            self.assertIn("copied to clipboard", result["message"])
        finally:
            temp_dir.cleanup()

    def test_scan_media_file_broadcasts_encoded_file_uri(self):
        runner = RunnerStub()
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        with mock.patch("auto_tiktok_editor.phone_control.time.time", return_value=1718424000), mock.patch.object(
            controller,
            "_finalize_media_file",
            return_value=True,
        ) as finalize:
            scanned = controller._scan_media_file(
                "192.168.1.20:5555",
                "/sdcard/DCIM/Camera/my video.mp4",
            )

        self.assertTrue(scanned)
        self.assertEqual(
            runner.commands[0][-4:],
            ["shell", "touch", "-m", "/sdcard/DCIM/Camera/my video.mp4"],
        )
        broadcast_command = runner.commands[1]
        self.assertEqual(broadcast_command[-2:], ["-d", "file:///sdcard/DCIM/Camera/my%20video.mp4"])
        self.assertIn("--receiver-include-background", broadcast_command)
        finalize.assert_called_once_with(
            "192.168.1.20:5555",
            "/sdcard/DCIM/Camera/my video.mp4",
            1718424000,
        )

    def test_send_file_to_gallery_connects_adb_pushes_media_and_scans_it(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            video_path = Path(temp_dir.name) / "my video.mp4"
            video_path.write_bytes(b"video-data")
            runner = RunnerStub()
            device_transfer = DeviceTransferStub()
            events = []
            controller = PhoneController(
                PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
                runner=runner,
                device_transfer=device_transfer,
                on_event=events.append,
            )

            with mock.patch.object(controller, "_scan_media_file", return_value=True) as scan:
                result = controller.send_file_to_gallery("192.168.1.20", video_path)

            remote_path = DEFAULT_PUSH_TARGET.rstrip("/") + "/my video.mp4"
            self.assertEqual(result["address"], "192.168.1.20:5555")
            self.assertEqual(result["remote_path"], remote_path)
            self.assertEqual(device_transfer.calls, [("wifi", "192.168.1.20:5555")])
            self.assertEqual(runner.tools, ["adb"])
            self.assertEqual(
                runner.commands[0],
                ["adb", "-s", "192.168.1.20:5555", "shell", "mkdir", "-p", DEFAULT_PUSH_TARGET],
            )
            self.assertEqual(
                runner.commands[1],
                ["adb", "-s", "192.168.1.20:5555", "push", str(video_path.resolve()), remote_path],
            )
            scan.assert_called_once_with("192.168.1.20:5555", remote_path)
            self.assertEqual(
                [event["action"] for event in events],
                [
                    "phone_transfer_started",
                    "phone_transfer_completed",
                    "phone_gallery_ready",
                ],
            )
        finally:
            temp_dir.cleanup()

    def test_file_size_reads_remote_stat_result(self):
        runner = RunnerStub([CompletedProcessStub(stdout="69285218\n")])
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        size = controller._file_size(
            "192.168.1.20:5555",
            "/sdcard/DCIM/Camera/video.mp4",
        )

        self.assertEqual(size, 69285218)
        self.assertEqual(
            runner.commands[-1][-3:],
            ["-c", "%s", "/sdcard/DCIM/Camera/video.mp4"],
        )

    def test_media_path_from_close_write_event(self):
        self.assertEqual(
            PhoneController._media_path_from_event(
                "w\t/sdcard/DCIM/Camera\tphoto.jpg\n"
            ),
            "/sdcard/DCIM/Camera/photo.jpg",
        )
        self.assertIsNone(
            PhoneController._media_path_from_event(
                "d\t/sdcard/DCIM/Camera\tphoto.jpg\n"
            )
        )
        self.assertIsNone(
            PhoneController._media_path_from_event(
                "w\t/sdcard/DCIM/Camera\tnotes.txt\n"
            )
        )

    def test_process_completed_media_file_emits_gallery_ready(self):
        events = []
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=RunnerStub(),
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        with mock.patch.object(controller, "_file_size", return_value=1024), mock.patch.object(
            controller,
            "_scan_media_file",
            return_value=True,
        ):
            controller._process_completed_media_file(
                "192.168.1.20:5555",
                "/sdcard/DCIM/Camera/photo.jpg",
            )

        self.assertEqual(
            [event["action"] for event in events],
            [
                "phone_transfer_started",
                "phone_transfer_completed",
                "phone_gallery_ready",
            ],
        )

    def test_finalize_media_file_clears_pending_row(self):
        runner = RunnerStub(
            [
                CompletedProcessStub(
                    stdout=(
                        "Row: 0 _id=123, "
                        "_data=/storage/emulated/0/DCIM/Camera/video.mp4, "
                        "is_pending=1\n"
                    )
                ),
                CompletedProcessStub(),
            ]
        )
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        finalized = controller._finalize_media_file(
            "192.168.1.20:5555",
            "/sdcard/DCIM/Camera/video.mp4",
            1718424000,
        )

        self.assertTrue(finalized)
        self.assertIn("content://media/external/video/media/123", runner.commands[-1])
        self.assertIn("is_pending:i:0", runner.commands[-1])
        self.assertIn("date_added:l:1718424000", runner.commands[-1])
        self.assertIn("date_modified:l:1718424000", runner.commands[-1])
        self.assertIn("datetaken:l:1718424000000", runner.commands[-1])

    def test_finalize_image_uses_media_images_collection(self):
        runner = RunnerStub(
            [
                CompletedProcessStub(
                    stdout=(
                        "Row: 0 _id=456, "
                        "_data=/storage/emulated/0/DCIM/Camera/photo.jpg, "
                        "is_pending=1\n"
                    )
                ),
                CompletedProcessStub(),
            ]
        )
        controller = PhoneController(
            PipelineConfig(adb_bin="adb", scrcpy_bin="scrcpy"),
            runner=runner,
            device_transfer=DeviceTransferStub(),
        )

        finalized = controller._finalize_media_file(
            "192.168.1.20:5555",
            "/sdcard/DCIM/Camera/photo.jpg",
        )

        self.assertTrue(finalized)
        self.assertIn("content://media/external/images/media", runner.commands[0])
        self.assertIn("content://media/external/images/media/456", runner.commands[-1])

    def test_emit_event_forwards_structured_payload(self):
        events = []
        controller = PhoneController(
            PipelineConfig(),
            runner=RunnerStub(),
            device_transfer=DeviceTransferStub(),
            on_event=events.append,
        )

        controller._emit_event(
            "info",
            "phone_gallery_ready",
            "Ready in Gallery.",
            file_name="video.mp4",
            size_bytes=123,
        )

        self.assertEqual(events[0]["action"], "phone_gallery_ready")
        self.assertEqual(events[0]["file_name"], "video.mp4")
        self.assertEqual(events[0]["size_bytes"], 123)


if __name__ == "__main__":
    unittest.main()
