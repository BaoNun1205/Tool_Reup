import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import unittest

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import MediaInfo, ProcessedMaster
from auto_tiktok_editor.media.scenes import SceneDetector


class FakeRunner(object):
    devnull = "NUL"

    def __init__(self, stderr=""):
        self.stderr = stderr
        self.commands = []

    def run(self, command, check=True, capture_output=False):
        self.commands.append(command)
        return SimpleNamespace(stderr=self.stderr)


def _processed_master(duration=5.0):
    return ProcessedMaster(
        path=Path("processed.mp4"),
        info=MediaInfo(
            path=Path("processed.mp4"),
            duration_seconds=duration,
            width=1080,
            height=1920,
            frame_rate=30.0,
            has_audio=True,
            audio_sample_rate=48000,
            video_codec="h264",
            audio_codec="aac",
        ),
        speed_factor=1.2,
    )


class SceneDetectorTests(unittest.TestCase):
    def test_fixed_mode_cuts_processed_video_into_configured_chunks(self):
        detector = SceneDetector(PipelineConfig(fixed_chunk_duration_seconds=2.27), FakeRunner())

        scenes = detector._detect_scene_boundaries(_processed_master(duration=5.0))

        self.assertEqual(len(scenes), 3)
        self.assertAlmostEqual(scenes[0].start_seconds, 0.0)
        self.assertAlmostEqual(scenes[0].end_seconds, 2.27)
        self.assertAlmostEqual(scenes[1].start_seconds, 2.27)
        self.assertAlmostEqual(scenes[1].end_seconds, 4.54)
        self.assertAlmostEqual(scenes[2].start_seconds, 4.54)
        self.assertAlmostEqual(scenes[2].end_seconds, 5.0)

    def test_scene_mode_uses_ffmpeg_scene_timestamps_as_boundaries(self):
        runner = FakeRunner(
            stderr=(
                "showinfo pts_time:1.2 other\n"
                "showinfo pts_time:3.7 other\n"
                "showinfo pts_time:3.7 duplicate\n"
            )
        )
        detector = SceneDetector(PipelineConfig(video_cut_mode="scene", scene_threshold=0.4), runner)

        scenes = detector._detect_scene_boundaries(_processed_master(duration=5.0))

        self.assertEqual([(scene.start_seconds, scene.end_seconds) for scene in scenes], [(0.0, 1.2), (1.2, 3.7), (3.7, 5.0)])
        self.assertIn("select=gt(scene\\,0.4),showinfo", runner.commands[0])

    def test_original_mode_keeps_one_full_video_range(self):
        runner = FakeRunner()
        detector = SceneDetector(PipelineConfig(video_cut_mode="original"), runner)

        scenes, black_ranges, warnings = detector.detect(_processed_master(duration=5.0))

        self.assertEqual([(scene.start_seconds, scene.end_seconds) for scene in scenes], [(0.0, 5.0)])
        self.assertEqual(black_ranges, [])
        self.assertEqual(warnings, [])
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
