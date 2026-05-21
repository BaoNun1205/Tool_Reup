"""Product image preprocessing before final overlay compositing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ImageInfo
from auto_tiktok_editor.exceptions import EditorError
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.utils.image_probe import probe_image


@dataclass
class ProductImagePreprocessResult:
    image_info: ImageInfo
    cropped_path: Path
    enhanced: bool = False
    warnings: List[str] = field(default_factory=list)


class ProductImagePreprocessor(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner

    def prepare(self, image_info: ImageInfo, output_dir: Path) -> ProductImagePreprocessResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        warnings = []
        cropped_path = output_dir / "product_4x3.png"
        try:
            self._crop_to_4_3(image_info.path, cropped_path)
            prepared_info = probe_image(cropped_path)
        except (EditorError, OSError) as exc:
            if self.config.product_image_enhance_required:
                raise
            warnings.append("Could not crop product image to 4:3 before enhancement; using original image. %s" % exc)
            return ProductImagePreprocessResult(
                image_info=image_info,
                cropped_path=image_info.path,
                enhanced=False,
                warnings=warnings,
            )

        if not self.config.product_image_enhance_enabled:
            return ProductImagePreprocessResult(
                image_info=prepared_info,
                cropped_path=cropped_path,
                enhanced=False,
                warnings=warnings,
            )

        enhanced_path = output_dir / "product_4x3_enhanced.png"
        try:
            self._enhance_with_realesrgan(cropped_path, enhanced_path)
            enhanced_info = probe_image(enhanced_path)
            return ProductImagePreprocessResult(
                image_info=enhanced_info,
                cropped_path=cropped_path,
                enhanced=True,
                warnings=warnings,
            )
        except (EditorError, OSError) as exc:
            if self.config.product_image_enhance_required:
                raise
            warnings.append("Real-ESRGAN product image enhancement was skipped; using cropped image. %s" % exc)
            return ProductImagePreprocessResult(
                image_info=prepared_info,
                cropped_path=cropped_path,
                enhanced=False,
                warnings=warnings,
            )

    def _crop_to_4_3(self, input_path: Path, output_path: Path) -> None:
        crop_filter = (
            "format=rgba,"
            "crop='min(iw\\,ih*4/3)':'min(ih\\,iw*3/4)':"
            "(iw-min(iw\\,ih*4/3))/2:(ih-min(ih\\,iw*3/4))/2"
        )
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-vf",
            crop_filter,
            "-frames:v",
            "1",
            str(output_path),
        ]
        self.runner.run(command)

    def _enhance_with_realesrgan(self, input_path: Path, output_path: Path) -> None:
        command = [
            self.config.realesrgan_bin,
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-n",
            self.config.product_image_enhance_model,
            "-s",
            str(self.config.product_image_enhance_scale),
        ]
        self.runner.run(command)
