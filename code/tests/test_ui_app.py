import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.ui.app import EditorApplication


class EditorApplicationTests(unittest.TestCase):
    def test_blur_percent_defaults_to_config_alpha_ratio(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig(split_separator_max_alpha_ratio=0.40)

        self.assertEqual(app._default_blur_percent(), 40)

    def test_alpha_ratio_mapping_matches_slider_percent(self):
        app = EditorApplication.__new__(EditorApplication)
        app.config = PipelineConfig()

        self.assertAlmostEqual(app._alpha_ratio_from_blur_percent(40), 0.40)
        self.assertAlmostEqual(app._alpha_ratio_from_blur_percent(95), 0.95)
        self.assertAlmostEqual(app._alpha_ratio_from_blur_percent(5), 0.05)


if __name__ == "__main__":
    unittest.main()
