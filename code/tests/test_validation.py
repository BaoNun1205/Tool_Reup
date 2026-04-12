import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import struct
import tempfile
import time
import os
import unittest

from auto_tiktok_editor.domain.models import JobSpec, SessionItemSpec, SessionSpec
from auto_tiktok_editor.domain.validation import InputValidator, SessionValidator
from auto_tiktok_editor.exceptions import SessionValidationError, ValidationError


def write_fake_png(path: Path, width: int, height: int, has_alpha: bool = True) -> None:
    color_type = 6 if has_alpha else 2
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    ihdr_chunk = struct.pack(">I4s", len(ihdr_data), b"IHDR") + ihdr_data + b"\x00\x00\x00\x00"
    iend_chunk = struct.pack(">I4s", 0, b"IEND") + b"\x00\x00\x00\x00"
    path.write_bytes(signature + ihdr_chunk + iend_chunk)


class InputValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.validator = InputValidator()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_accepts_valid_tiktok_url_and_png(self):
        image_path = self.base_dir / "product.png"
        write_fake_png(image_path, 900, 900, has_alpha=True)
        spec = JobSpec(
            source_video_url="https://www.tiktok.com/@store/video/1234567890",
            product_image=image_path,
            output_dir=self.base_dir / "out",
        )
        result = self.validator.validate(spec)
        self.assertEqual(result.image_info.image_type, "png")
        self.assertTrue(result.image_info.has_alpha)
        self.assertEqual(result.warnings, [])

    def test_warns_for_small_but_accepted_image(self):
        image_path = self.base_dir / "small.png"
        write_fake_png(image_path, 500, 500, has_alpha=True)
        spec = JobSpec(
            source_video_url="https://vm.tiktok.com/abcdef/",
            product_image=image_path,
            output_dir=self.base_dir / "out",
        )
        result = self.validator.validate(spec)
        self.assertEqual(len(result.warnings), 1)

    def test_rejects_non_tiktok_url(self):
        image_path = self.base_dir / "product.png"
        write_fake_png(image_path, 900, 900, has_alpha=True)
        spec = JobSpec(
            source_video_url="https://example.com/video/1",
            product_image=image_path,
            output_dir=self.base_dir / "out",
        )
        with self.assertRaises(ValidationError):
            self.validator.validate(spec)

    def test_rejects_too_small_image(self):
        image_path = self.base_dir / "tiny.png"
        write_fake_png(image_path, 320, 320, has_alpha=True)
        spec = JobSpec(
            source_video_url="https://www.tiktok.com/@store/video/1234567890",
            product_image=image_path,
            output_dir=self.base_dir / "out",
        )
        with self.assertRaises(ValidationError):
            self.validator.validate(spec)

    def test_warns_when_cookies_file_has_no_tiktokv_domains(self):
        image_path = self.base_dir / "product.png"
        write_fake_png(image_path, 900, 900, has_alpha=True)
        cookies_path = self.base_dir / "cookies.txt"
        cookies_path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n",
            encoding="utf-8",
        )
        spec = JobSpec(
            source_video_url="https://www.tiktok.com/@store/video/1234567890",
            product_image=image_path,
            output_dir=self.base_dir / "out",
            cookies_file=cookies_path,
        )
        result = InputValidator(config=self.validator.config.__class__(download_via_lazy_down_only=False)).validate(spec)
        self.assertTrue(any("tiktokv.com" in warning for warning in result.warnings))

    def test_warns_when_cookies_file_is_older_than_fresh_session_window(self):
        image_path = self.base_dir / "product.png"
        write_fake_png(image_path, 900, 900, has_alpha=True)
        cookies_path = self.base_dir / "cookies.txt"
        cookies_path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n"
            ".tiktokv.com\tTRUE\t/\tTRUE\t0\todin_tt\txyz789\n",
            encoding="utf-8",
        )
        stale_seconds = self.validator.config.browser_cookie_freshness_seconds + 300
        stale_time = time.time() - stale_seconds
        cookies_path.touch()
        os.utime(cookies_path, (stale_time, stale_time))
        spec = JobSpec(
            source_video_url="https://www.tiktok.com/@store/video/1234567890",
            product_image=image_path,
            output_dir=self.base_dir / "out",
            cookies_file=cookies_path,
        )
        result = InputValidator(config=self.validator.config.__class__(download_via_lazy_down_only=False)).validate(spec)
        self.assertTrue(any("older than" in warning for warning in result.warnings))

    def test_ignores_cookie_warnings_when_lazy_down_only_is_enabled(self):
        image_path = self.base_dir / "product.png"
        write_fake_png(image_path, 900, 900, has_alpha=True)
        cookies_path = self.base_dir / "cookies.txt"
        cookies_path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n",
            encoding="utf-8",
        )
        spec = JobSpec(
            source_video_url="https://www.tiktok.com/@store/video/1234567890",
            product_image=image_path,
            output_dir=self.base_dir / "out",
            cookies_file=cookies_path,
        )
        result = self.validator.validate(spec)
        self.assertEqual(result.warnings, [])


class SessionValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.validator = SessionValidator()
        self.image_path = self.base_dir / "product.png"
        self.cookies_path = self.base_dir / "cookies.txt"
        write_fake_png(self.image_path, 900, 900, has_alpha=True)
        self.cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_validator_blocks_blank_rows(self):
        session = SessionSpec(
            items=[
                SessionItemSpec(row_id="row_001", source_video_url="", product_image=None),
                SessionItemSpec(
                    row_id="row_002",
                    source_video_url="https://www.tiktok.com/@store/video/1234567890",
                    product_image=self.image_path,
                ),
            ],
            output_root_dir=self.base_dir / "out",
        )
        with self.assertRaises(SessionValidationError) as context:
            self.validator.validate(session)
        self.assertIn(0, context.exception.row_errors)
        self.assertTrue(any("TikTok URL" in message for message in context.exception.row_errors[0]))

    def test_session_validator_accepts_duplicate_rows_with_warning(self):
        session = SessionSpec(
            items=[
                SessionItemSpec(
                    row_id="row_001",
                    source_video_url="https://www.tiktok.com/@store/video/1234567890",
                    product_image=self.image_path,
                ),
                SessionItemSpec(
                    row_id="row_002",
                    source_video_url="https://www.tiktok.com/@store/video/1234567890",
                    product_image=self.image_path,
                ),
            ],
            output_root_dir=self.base_dir / "out",
        )
        validated = self.validator.validate(session)
        self.assertEqual(len(validated.items), 2)
        self.assertEqual(len(validated.warnings), 1)
        self.assertIn("duplicates the same source/image pair", validated.warnings[0])

    def test_session_validator_accepts_existing_cookies_file(self):
        session = SessionSpec(
            items=[
                SessionItemSpec(
                    row_id="row_001",
                    source_video_url="https://www.tiktok.com/@store/video/1234567890",
                    product_image=self.image_path,
                ),
            ],
            output_root_dir=self.base_dir / "out",
            cookies_file=self.cookies_path,
        )
        validated = self.validator.validate(session)
        self.assertIsNone(validated.session_spec.cookies_file)
        self.assertIsNone(validated.items[0].validated_job.job_spec.cookies_file)

    def test_session_validator_ignores_missing_cookies_file_when_lazy_down_only(self):
        session = SessionSpec(
            items=[
                SessionItemSpec(
                    row_id="row_001",
                    source_video_url="https://www.tiktok.com/@store/video/1234567890",
                    product_image=self.image_path,
                ),
            ],
            output_root_dir=self.base_dir / "out",
            cookies_file=self.base_dir / "missing_cookies.txt",
        )
        validated = self.validator.validate(session)
        self.assertIsNone(validated.session_spec.cookies_file)


if __name__ == "__main__":
    unittest.main()
