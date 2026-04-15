import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import tempfile
import unittest
from types import SimpleNamespace

from auto_tiktok_editor.app.artifacts import ArtifactExporter
from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import (
    EditPlan,
    FinalAudioAsset,
    ImageInfo,
    MediaInfo,
    OverlaySpec,
    PreparedAudioAsset,
    ProcessedMaster,
    RoughCutAsset,
    SceneRange,
    SessionItemSpec,
    SessionSpec,
    SourceAsset,
    ValidatedJob,
    WorkingMedia,
)
from auto_tiktok_editor.domain.validation import SessionValidator
from auto_tiktok_editor.exceptions import DownloadError


class FakeValidator(object):
    def validate(self, job_spec):
        return ValidatedJob(
            job_spec=job_spec,
            image_info=ImageInfo(
                path=job_spec.product_image,
                width=900,
                height=900,
                mime_type="image/png",
                image_type="png",
                has_alpha=True,
            ),
            warnings=[],
        )


class FakeDownloader(object):
    def download(self, source_url, destination_dir, cookies_file=None):
        if "fail" in source_url:
            raise DownloadError("Synthetic download failure for test coverage.")
        source_path = destination_dir / "source.mp4"
        source_path.write_text("source", encoding="utf-8")
        return SourceAsset(
            source_url=source_url,
            downloaded_path=source_path,
            extractor_name="fake",
            metadata={
                "download_strategy": "cookies_file" if cookies_file else "direct",
                "source_title": "Demo title #xuhuong #demo",
                "source_author": "demo-author",
                "source_unique_id": "demo-uid",
            },
        )


class FakeProbe(object):
    def probe(self, media_path):
        return MediaInfo(
            path=media_path,
            duration_seconds=6.0,
            width=1080,
            height=1920,
            frame_rate=30.0,
            has_audio=True,
            audio_sample_rate=48000,
            video_codec="h264",
            audio_codec="aac",
        )


class FakeNormalizer(object):
    def normalize(self, source_asset, source_info, output_path):
        output_path.write_text("normalized", encoding="utf-8")
        return WorkingMedia(path=output_path, info=source_info, warnings=[])


class FakeSpeedProcessor(object):
    def process(self, working_media, output_path):
        output_path.write_text("processed", encoding="utf-8")
        return ProcessedMaster(path=output_path, info=working_media.info, speed_factor=1.2)


class FakeSceneDetector(object):
    def detect(self, processed_master):
        return [
            SceneRange(0.0, 1.5, 0),
            SceneRange(1.5, 3.0, 1),
            SceneRange(3.0, 4.5, 2),
            SceneRange(4.5, 6.0, 3),
        ], [], []


class FakeSceneQualifier(object):
    def qualify(self, raw_scenes, black_ranges):
        return raw_scenes, [], []


class FakeEditPlanner(object):
    def build(self, scenes, seed):
        return EditPlan(seed=seed or 99, opener_index=0, closer_index=3, ordered_scenes=list(scenes), warnings=[])


class FakeRoughCutRenderer(object):
    def render(self, processed_master, edit_plan, clips_dir, output_path):
        clips_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text("roughcut", encoding="utf-8")
        clip_path = clips_dir / "clip_000.mp4"
        clip_path.write_text("clip", encoding="utf-8")
        return RoughCutAsset(path=output_path, info=processed_master.info, clip_paths=[clip_path])


class FakeOverlayPlanner(object):
    def plan(self, image_info, separator_max_alpha_ratio=None):
        return OverlaySpec(
            source_image_path=image_info.path,
            image_type=image_info.image_type,
            mode="png_alpha_feather",
            x=48,
            y=48,
            content_width=300,
            content_height=300,
            panel_width=300,
            panel_height=300,
            padding=0,
            shadow_offset=12,
            warnings=[],
            separator_max_alpha_ratio=separator_max_alpha_ratio,
        )


class FakeAudioFinisher(object):
    def __init__(self):
        self.prepared_inputs = []
        self.finished_inputs = []

    def prepare(self, processed_master, output_path):
        self.prepared_inputs.append(processed_master.path)
        output_path.write_text("prepared-audio", encoding="utf-8")
        return PreparedAudioAsset(path=output_path, has_audio=True, duration_seconds=processed_master.info.duration_seconds, warnings=[])

    def finish(self, prepared_audio, duration_seconds, output_path):
        self.finished_inputs.append((prepared_audio.path, duration_seconds))
        output_path.write_text("audio", encoding="utf-8")
        return FinalAudioAsset(path=output_path, has_audio=True, warnings=[])


class FakeFinalCompositor(object):
    def compose(self, rough_cut, final_audio, overlay_spec, output_path):
        output_path.write_text("video", encoding="utf-8")
        return output_path


class SessionSmokeTests(unittest.TestCase):
    def build_services(self):
        audio_finisher = FakeAudioFinisher()
        return SimpleNamespace(
            validator=FakeValidator(),
            downloader=FakeDownloader(),
            probe=FakeProbe(),
            normalizer=FakeNormalizer(),
            speed_processor=FakeSpeedProcessor(),
            scene_detector=FakeSceneDetector(),
            scene_qualifier=FakeSceneQualifier(),
            edit_planner=FakeEditPlanner(),
            overlay_planner=FakeOverlayPlanner(),
            rough_cut_renderer=FakeRoughCutRenderer(),
            audio_finisher=audio_finisher,
            final_compositor=FakeFinalCompositor(),
            artifact_exporter=ArtifactExporter(),
        )

    def test_session_orchestrator_completes_multiple_items(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base_dir = Path(temp_dir.name)
            image_path = base_dir / "product.png"
            image_path.write_text("fake-image", encoding="utf-8")
            cookies_path = base_dir / "cookies.txt"
            cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            config = PipelineConfig(default_output_root=base_dir / "out")
            services = self.build_services()
            validator = SessionValidator(item_validator=FakeValidator(), config=config)
            orchestrator = SessionOrchestrator(config=config, services=services, session_validator=validator)
            result = orchestrator.run(
                SessionSpec(
                    items=[
                        SessionItemSpec(
                            row_id="row_001",
                            source_video_url="https://www.tiktok.com/@store/video/1234567890",
                            product_image=image_path,
                        ),
                        SessionItemSpec(
                            row_id="row_002",
                            source_video_url="https://www.tiktok.com/@store/video/1234567891",
                            product_image=image_path,
                        ),
                    ],
                    output_root_dir=base_dir / "out",
                    session_name="test-session",
                    cookies_file=cookies_path,
                )
            )
            self.assertEqual(result.status, "completed_with_success")
            self.assertEqual(len(result.items), 2)
            self.assertIsNone(result.artifacts.summary_path)
            self.assertFalse(result.artifacts.is_finalized)
            self.assertIsNone(result.artifacts.titles_path)
            self.assertTrue((result.artifacts.session_dir / "items").exists())
            self.assertIsNone(result.summary["cookies_file"])
            for item in result.items:
                self.assertEqual(item.status, "completed")
                self.assertEqual(item.metadata["download_strategy_used"], "direct")
                self.assertEqual(item.metadata["source_title"], "Demo title #xuhuong #demo")
                self.assertTrue(item.metadata["audio_extracted_before_shuffle"])
                self.assertTrue(item.artifacts.final_video_path.exists())
                self.assertTrue(item.artifacts.final_audio_path.exists())
                self.assertIsNone(item.artifacts.metadata_path)
                self.assertIsNone(item.artifacts.process_log_path)
                self.assertIsNone(item.artifacts.video_title_path)
            finalized = orchestrator.finalize_reviewed_session(result)
            self.assertTrue(finalized.artifacts.is_finalized)
            self.assertTrue(finalized.artifacts.titles_path.exists())
            self.assertEqual(
                finalized.artifacts.titles_path.read_text(encoding="utf-8"),
                "Demo title #xuhuong #demo\n\nDemo title #xuhuong #demo\n",
            )
            self.assertTrue((finalized.artifacts.session_dir / "002_final_video.mp4").exists())
            self.assertTrue((finalized.artifacts.session_dir / "001_final_video.mp4").exists())
            self.assertFalse((finalized.artifacts.session_dir / "items").exists())
            for item in finalized.items:
                self.assertIsNone(item.artifacts.final_audio_path)
            self.assertEqual(len(services.audio_finisher.prepared_inputs), 2)
            self.assertEqual(len(services.audio_finisher.finished_inputs), 2)
        finally:
            temp_dir.cleanup()

    def test_session_orchestrator_continues_after_item_failure(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base_dir = Path(temp_dir.name)
            image_path = base_dir / "product.png"
            image_path.write_text("fake-image", encoding="utf-8")
            config = PipelineConfig(default_output_root=base_dir / "out")
            services = self.build_services()
            validator = SessionValidator(item_validator=FakeValidator(), config=config)
            orchestrator = SessionOrchestrator(config=config, services=services, session_validator=validator)
            result = orchestrator.run(
                SessionSpec(
                    items=[
                        SessionItemSpec(
                            row_id="row_001",
                            source_video_url="https://www.tiktok.com/@store/video/fail-case",
                            product_image=image_path,
                        ),
                        SessionItemSpec(
                            row_id="row_002",
                            source_video_url="https://www.tiktok.com/@store/video/1234567891",
                            product_image=image_path,
                        ),
                    ],
                    output_root_dir=base_dir / "out",
                )
            )
            self.assertEqual(result.status, "completed_with_partial_failure")
            self.assertEqual(len(result.items), 2)
            self.assertEqual(result.summary["item_count_completed"], 1)
            self.assertEqual(result.summary["item_count_failed"], 1)
            self.assertEqual(result.items[0].status, "failed")
            self.assertEqual(result.items[1].status, "completed")
            self.assertIsNone(result.artifacts.summary_path)
            self.assertFalse(result.artifacts.is_finalized)
            self.assertIsNone(result.artifacts.titles_path)
            finalized = orchestrator.finalize_reviewed_session(result)
            self.assertTrue(finalized.artifacts.titles_path.exists())
            self.assertTrue((finalized.artifacts.session_dir / "001_final_video.mp4").exists())
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
