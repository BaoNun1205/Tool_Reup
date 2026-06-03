import struct
import sys
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tempfile
import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.exceptions import ExternalToolError
from auto_tiktok_editor.media.product_image import ProductImagePreprocessor
from auto_tiktok_editor.utils.image_probe import probe_image


TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def write_fake_png(path: Path, width: int, height: int, has_alpha: bool = True) -> None:
    color_type = 6 if has_alpha else 2
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    ihdr_chunk = struct.pack(">I4s", len(ihdr_data), b"IHDR") + ihdr_data + b"\x00\x00\x00\x00"
    iend_chunk = struct.pack(">I4s", 0, b"IEND") + b"\x00\x00\x00\x00"
    path.write_bytes(signature + ihdr_chunk + iend_chunk)


def cleanup_temp_dir(temp_dir) -> None:
    path = temp_dir.name
    temp_dir._finalizer.detach()
    shutil.rmtree(path, ignore_errors=True)


class ProductImageRunner(object):
    def __init__(self, fail_realesrgan=False):
        self.commands = []
        self.fail_realesrgan = fail_realesrgan

    def run(self, args, cwd=None, check=True, capture_output=True):
        self.commands.append(list(args))
        tool = Path(args[0]).name.lower()
        if "realesrgan" in tool:
            if self.fail_realesrgan:
                raise ExternalToolError("Real-ESRGAN not available.")
            output_path = Path(args[args.index("-o") + 1])
            write_fake_png(output_path, 4800, 4800, has_alpha=True)
            return None
        write_fake_png(Path(args[-1]), 1200, 1200, has_alpha=True)
        return None


class ProductImagePreprocessorTests(unittest.TestCase):
    def test_crops_to_1_1_before_realesrgan_enhancement(self):
        temp_dir = tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))
        try:
            base_dir = Path(temp_dir.name)
            source_path = base_dir / "product.png"
            write_fake_png(source_path, 1600, 1200, has_alpha=True)
            runner = ProductImageRunner()
            config = PipelineConfig(
                ffmpeg_bin="ffmpeg",
                realesrgan_bin="realesrgan-ncnn-vulkan",
                product_image_enhance_enabled=True,
            )
            preprocessor = ProductImagePreprocessor(config, runner)

            result = preprocessor.prepare(probe_image(source_path), base_dir / "processed")

            self.assertTrue(result.enhanced)
            self.assertEqual(result.image_info.path.name, "product_1x1_enhanced.png")
            self.assertEqual((result.image_info.width, result.image_info.height), (4800, 4800))
            crop_command = runner.commands[0]
            enhance_command = runner.commands[1]
            self.assertIn("crop='min(iw\\,ih)':'min(iw\\,ih)'", crop_command[crop_command.index("-vf") + 1])
            self.assertEqual(enhance_command[0], "realesrgan-ncnn-vulkan")
            self.assertEqual(enhance_command[enhance_command.index("-i") + 1], str(result.cropped_path))
            self.assertEqual(enhance_command[enhance_command.index("-s") + 1], "4")
        finally:
            cleanup_temp_dir(temp_dir)

    def test_uses_cropped_image_when_realesrgan_is_unavailable(self):
        temp_dir = tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))
        try:
            base_dir = Path(temp_dir.name)
            source_path = base_dir / "product.png"
            write_fake_png(source_path, 1600, 1200, has_alpha=True)
            runner = ProductImageRunner(fail_realesrgan=True)
            preprocessor = ProductImagePreprocessor(PipelineConfig(), runner)

            result = preprocessor.prepare(probe_image(source_path), base_dir / "processed")

            self.assertFalse(result.enhanced)
            self.assertEqual(result.image_info.path.name, "product_1x1.png")
            self.assertEqual((result.image_info.width, result.image_info.height), (1200, 1200))
            self.assertEqual(len(result.warnings), 1)
        finally:
            cleanup_temp_dir(temp_dir)


if __name__ == "__main__":
    unittest.main()
