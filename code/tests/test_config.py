import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import os
import unittest

from auto_tiktok_editor.config import PipelineConfig


class PipelineConfigTests(unittest.TestCase):
    def test_from_env_overrides_binary_names(self):
        old_ffmpeg = os.environ.get("AUTO_EDITOR_FFMPEG_BIN")
        old_ffprobe = os.environ.get("AUTO_EDITOR_FFPROBE_BIN")
        old_ytdlp = os.environ.get("AUTO_EDITOR_YTDLP_BIN")
        old_lazy_only = os.environ.get("AUTO_EDITOR_LAZY_DOWN_ONLY")
        try:
            os.environ["AUTO_EDITOR_FFMPEG_BIN"] = "custom-ffmpeg"
            os.environ["AUTO_EDITOR_FFPROBE_BIN"] = "custom-ffprobe"
            os.environ["AUTO_EDITOR_YTDLP_BIN"] = "custom-ytdlp"
            os.environ["AUTO_EDITOR_LAZY_DOWN_ONLY"] = "false"
            config = PipelineConfig.from_env()
            self.assertEqual(config.ffmpeg_bin, "custom-ffmpeg")
            self.assertEqual(config.ffprobe_bin, "custom-ffprobe")
            self.assertEqual(config.ytdlp_bin, "custom-ytdlp")
            self.assertFalse(config.download_via_lazy_down_only)
        finally:
            self._restore("AUTO_EDITOR_FFMPEG_BIN", old_ffmpeg)
            self._restore("AUTO_EDITOR_FFPROBE_BIN", old_ffprobe)
            self._restore("AUTO_EDITOR_YTDLP_BIN", old_ytdlp)
            self._restore("AUTO_EDITOR_LAZY_DOWN_ONLY", old_lazy_only)

    def test_build_job_id_has_prefix_and_suffix(self):
        config = PipelineConfig()
        job_id = config.build_job_id()
        self.assertIn("_", job_id)
        self.assertGreaterEqual(len(job_id), 10)

    def test_build_session_id_has_session_prefix(self):
        config = PipelineConfig()
        session_id = config.build_session_id()
        self.assertTrue(session_id.startswith("session_"))
        self.assertGreaterEqual(len(session_id), 18)

    def _restore(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
