"""Service assembly helpers for the default pipeline implementation."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.planner import EditPlanner, SceneQualifier
from auto_tiktok_editor.domain.validation import InputValidator
from auto_tiktok_editor.media.audio import AudioFinisher
from auto_tiktok_editor.media.downloader import SourceDownloader
from auto_tiktok_editor.media.normalize import MediaNormalizer
from auto_tiktok_editor.media.overlay import OverlayPlanner
from auto_tiktok_editor.media.probe import MediaProbe
from auto_tiktok_editor.media.product_image import ProductImagePreprocessor
from auto_tiktok_editor.media.render import BackgroundRemovalCompositor, FinalCompositor, RoughCutRenderer
from auto_tiktok_editor.media.scenes import SceneDetector
from auto_tiktok_editor.media.speed import SpeedProcessor
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.app.artifacts import ArtifactExporter
from auto_tiktok_editor.app.device_transfer import AndroidDeviceTransfer


@dataclass
class PipelineServices:
    validator: object
    downloader: object
    probe: object
    normalizer: object
    speed_processor: object
    scene_detector: object
    scene_qualifier: object
    edit_planner: object
    product_image_preprocessor: object
    overlay_planner: object
    rough_cut_renderer: object
    audio_finisher: object
    final_compositor: object
    background_removal_compositor: object
    artifact_exporter: object
    device_transfer: object


def build_default_services(config: PipelineConfig) -> PipelineServices:
    logger = logging.getLogger("auto_tiktok_editor")
    runner = CommandRunner(logger=logger)
    probe = MediaProbe(config, runner)
    return PipelineServices(
        validator=InputValidator(config=config),
        downloader=SourceDownloader(config, runner),
        probe=probe,
        normalizer=MediaNormalizer(config, runner, probe),
        speed_processor=SpeedProcessor(config, runner, probe),
        scene_detector=SceneDetector(config, runner),
        scene_qualifier=SceneQualifier(config),
        edit_planner=EditPlanner(config),
        product_image_preprocessor=ProductImagePreprocessor(config, runner),
        overlay_planner=OverlayPlanner(config),
        rough_cut_renderer=RoughCutRenderer(config, runner, probe),
        audio_finisher=AudioFinisher(config, runner),
        final_compositor=FinalCompositor(config, runner),
        background_removal_compositor=BackgroundRemovalCompositor(config, runner),
        artifact_exporter=ArtifactExporter(),
        device_transfer=AndroidDeviceTransfer(config, runner, logger=logger),
    )
