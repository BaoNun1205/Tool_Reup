"""Normalize source media into a stable working format."""

from __future__ import annotations

from pathlib import Path
from typing import List

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import MediaInfo, SourceAsset, WorkingMedia
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.media.probe import MediaProbe


class MediaNormalizer(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner, probe: MediaProbe):
        self.config = config
        self.runner = runner
        self.probe = probe

    def normalize(self, source_asset: SourceAsset, source_info: MediaInfo, output_path: Path) -> WorkingMedia:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        warnings = self._build_warnings(source_info)
        zoom_expr = "%0.4f" % self.config.split_zoom_factor
        visible_height = max(
            2,
            self._even_int(
                (self.config.target_height - self.config.split_video_trim_top_pixels - self.config.split_video_trim_bottom_pixels)
                * self.config.split_zoom_factor
            ),
        )
        vf = (
            "scale=%d:%d:force_original_aspect_ratio=increase," % (self.config.target_width, self.config.target_height)
            + "crop=%d:%d:0:%d," % (
                self.config.target_width,
                self.config.target_height - self.config.split_video_trim_top_pixels - self.config.split_video_trim_bottom_pixels,
                self.config.split_video_trim_top_pixels,
            )
            + "scale=trunc(iw*%s/2)*2:trunc(ih*%s/2)*2," % (zoom_expr, zoom_expr)
            + "crop=%d:%d:(iw-%d)/2:0," % (
                self.config.target_width,
                visible_height,
                self.config.target_width,
            )
            + "fps=%d,format=yuv420p" % self.config.target_fps
        )
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(source_asset.downloaded_path),
            "-map",
            "0:v:0",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        if source_info.has_audio:
            command.extend(
                [
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "aac",
                    "-ar",
                    str(self.config.target_sample_rate),
                    "-ac",
                    "2",
                ]
            )
        else:
            command.append("-an")
        command.append(str(output_path))
        self.runner.run(command)
        normalized_info = self.probe.probe(output_path)
        return WorkingMedia(path=output_path, info=normalized_info, warnings=warnings)

    def _even_int(self, value: float) -> int:
        rounded = int(round(value))
        if rounded % 2 != 0:
            rounded += 1
        return rounded

    def _build_warnings(self, source_info: MediaInfo) -> List[str]:
        warnings = []
        if source_info.width and source_info.height:
            aspect_ratio = float(source_info.width) / float(source_info.height)
            if source_info.width >= source_info.height:
                warnings.append("Source video is not portrait; center crop normalization was applied.")
            elif abs(aspect_ratio - (9.0 / 16.0)) > 0.18:
                warnings.append("Source portrait aspect ratio deviates from target; center crop normalization was applied.")
        if not source_info.has_audio:
            warnings.append("Source video has no audio track; pipeline will generate silent final audio.")
        return warnings
