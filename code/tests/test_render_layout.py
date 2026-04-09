import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import FinalAudioAsset, ImageInfo, MediaInfo, RoughCutAsset
from auto_tiktok_editor.media.overlay import OverlayPlanner
from auto_tiktok_editor.media.render import FinalCompositor
from auto_tiktok_editor.utils.command import CommandRunner


class RecordingRunner(object):
    def __init__(self):
        self.commands = []

    def run(self, args, cwd=None, check=True, capture_output=True):
        self.commands.append(list(args))
        return None


class RenderLayoutTests(unittest.TestCase):
    def test_split_layout_filter_matches_requested_crop_and_blend(self):
        config = PipelineConfig()
        planner = OverlayPlanner(config)
        spec = planner.plan(
            ImageInfo(
                path=Path('product.png'),
                width=1600,
                height=1200,
                mime_type='image/jpeg',
                image_type='jpg',
                has_alpha=False,
            )
        )
        compositor = FinalCompositor(config, CommandRunner())

        filter_complex = compositor._build_filter_complex(spec)

        self.assertEqual(spec.mode, 'stacked_split_mask')
        self.assertIn('crop=iw:trunc(ih*0.8000/2)*2:0:0', filter_complex)
        self.assertIn('scale=1080:1920:force_original_aspect_ratio=increase', filter_complex)
        self.assertIn('scale=trunc(iw*1.1000/2)*2:trunc(ih*1.1000/2)*2', filter_complex)
        self.assertIn('crop=1080:1920:(iw-1080)/2:max(0\\,(ih-1920)*0.6000)[basev]', filter_complex)
        self.assertIn("crop='min(iw\\,ih)':'min(iw\\,ih)'", filter_complex)
        self.assertIn('scale=1080:999:force_original_aspect_ratio=increase', filter_complex)
        self.assertIn('crop=1080:999:(iw-1080)/2:ih-999[imgcrop]', filter_complex)
        self.assertIn("geq=lum='if(lte(Y\\,346)\\,255*Y/346\\,255)'", filter_complex)
        self.assertIn('overlay=0:0:shortest=1:eof_action=pass,format=rgba[bottomsrc]', filter_complex)
        self.assertIn('[maskraw]boxblur=22:1[bottommask]', filter_complex)
        self.assertIn('[bottomsrc][bottommask]alphamerge[bottom]', filter_complex)
        self.assertIn('[basev][bottom]overlay=0:921:shortest=1:eof_action=pass[vraw]', filter_complex)
        self.assertIn('[vraw]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[vout]', filter_complex)

    def test_final_compositor_caps_to_rough_cut_without_shortest_mux_truncation(self):
        config = PipelineConfig()
        planner = OverlayPlanner(config)
        spec = planner.plan(
            ImageInfo(
                path=Path('product.png'),
                width=1600,
                height=1200,
                mime_type='image/jpeg',
                image_type='jpg',
                has_alpha=False,
            )
        )
        runner = RecordingRunner()
        compositor = FinalCompositor(config, runner)

        compositor.compose(
            RoughCutAsset(
                path=Path('rough_cut.mp4'),
                info=MediaInfo(
                    path=Path('rough_cut.mp4'),
                    duration_seconds=17.133333,
                    width=1080,
                    height=1920,
                    frame_rate=30.0,
                    has_audio=True,
                    audio_sample_rate=48000,
                    video_codec='h264',
                    audio_codec='aac',
                ),
                clip_paths=[],
            ),
            FinalAudioAsset(path=Path('final_audio.m4a'), has_audio=True, warnings=[]),
            spec,
            Path('final_video.mp4'),
        )

        command = runner.commands[0]
        self.assertIn('-t', command)
        self.assertIn('17.133', command)
        self.assertNotIn('-shortest', command)


if __name__ == '__main__':
    unittest.main()
