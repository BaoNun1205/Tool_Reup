import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import FinalAudioAsset, ImageInfo, MediaInfo, RoughCutAsset
from auto_tiktok_editor.media.normalize import MediaNormalizer
from auto_tiktok_editor.media.overlay import OverlayPlanner
from auto_tiktok_editor.media.render import FinalCompositor
from auto_tiktok_editor.utils.command import CommandRunner


class RecordingRunner(object):
    def __init__(self):
        self.commands = []

    def run(self, args, cwd=None, check=True, capture_output=True):
        self.commands.append(list(args))
        return None


class FakeProbe(object):
    def probe(self, path):
        return MediaInfo(
            path=path,
            duration_seconds=1.0,
            width=1080,
            height=1920,
            frame_rate=30.0,
            has_audio=True,
            audio_sample_rate=48000,
            video_codec='h264',
            audio_codec='aac',
        )


class RenderLayoutTests(unittest.TestCase):
    def test_normalizer_bakes_vertical_shift_before_scene_detection(self):
        config = PipelineConfig()
        runner = RecordingRunner()
        normalizer = MediaNormalizer(config, runner, FakeProbe())

        normalizer.normalize(
            type('SourceAssetStub', (), {'downloaded_path': Path('source.mp4')})(),
            MediaInfo(
                path=Path('source.mp4'),
                duration_seconds=5.0,
                width=1080,
                height=1920,
                frame_rate=30.0,
                has_audio=True,
                audio_sample_rate=48000,
                video_codec='h264',
                audio_codec='aac',
            ),
            Path('normalized.mp4'),
        )

        command = runner.commands[0]
        vf = command[command.index('-vf') + 1]
        self.assertIn('scale=1080:1920:force_original_aspect_ratio=increase', vf)
        self.assertIn('crop=1080:1440:0:200', vf)
        self.assertIn('scale=trunc(iw*1.0300/2)*2:trunc(ih*1.0300/2)*2', vf)
        self.assertIn('crop=1080:1482:(iw-1080)/2:0', vf)
        self.assertIn('eq=brightness=0.0200:contrast=0.9900:saturation=1.0300', vf)
        self.assertIn('cas=strength=0.1200', vf)

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
        self.assertIn('[0:v]scale=1080:-2:flags=lanczos,setsar=1,pad=1080:1920:0:0:color=black[basev]', filter_complex)
        self.assertIn("crop='min(iw\\,ih)':'min(iw\\,ih)'", filter_complex)
        self.assertIn("scale=w='1080.0000+152.0000*(0.5-0.5*cos(2*PI*n/180.0000))':h=-2:flags=lanczos:eval=frame", filter_complex)
        self.assertIn("crop=1080:1080:'min(max(0\\,(iw-1080)/2+56*sin(2*PI*n/150.0000))\\,iw-1080)':'min(max(0\\,ih-1080-41*(0.5-0.5*cos(2*PI*n/150.0000)))\\,ih-1080)'[imgcrop]", filter_complex)
        self.assertIn("geq=lum='if(lte(Y\\,157)\\,0\\,if(lte(Y\\,597)\\,255*pow((Y-157)/440\\,1.4800)\\,255))'", filter_complex)
        self.assertIn('overlay=0:H-h:shortest=1:eof_action=pass,format=rgba[bottomsrc]', filter_complex)
        self.assertIn('[maskraw]boxblur=28:1[bottommask]', filter_complex)
        self.assertIn('[bottomsrc][bottommask]alphamerge[bottom]', filter_complex)
        self.assertIn('[basev][bottom]overlay=0:683:shortest=1:eof_action=pass[vraw]', filter_complex)
        self.assertIn('[vraw]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[vout]', filter_complex)

    def test_split_layout_respects_custom_overlay_fade_ratio(self):
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
            ),
            separator_fade_ratio=0.25,
        )
        compositor = FinalCompositor(config, CommandRunner())

        filter_complex = compositor._build_filter_complex(spec)

        self.assertIn("geq=lum='if(lte(Y\\,157)\\,0\\,if(lte(Y\\,327)\\,255*pow((Y-157)/170\\,1.4800)\\,255))'", filter_complex)
        self.assertIn('[maskraw]boxblur=12:1[bottommask]', filter_complex)

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
        self.assertIn('-profile:v', command)
        self.assertIn('high', command)
        self.assertIn('-b:v', command)
        self.assertIn('16M', command)
        self.assertIn('-maxrate', command)
        self.assertIn('20M', command)
        self.assertIn('-b:a', command)
        self.assertIn('192k', command)
        self.assertIn('-colorspace', command)
        self.assertIn('bt709', command)


if __name__ == '__main__':
    unittest.main()
