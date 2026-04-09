"""Apply the fixed 1.2x speed processing stage."""

from __future__ import annotations

from pathlib import Path

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ProcessedMaster, WorkingMedia
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.media.probe import MediaProbe


class SpeedProcessor(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner, probe: MediaProbe):
        self.config = config
        self.runner = runner
        self.probe = probe

    def process(self, working_media: WorkingMedia, output_path: Path) -> ProcessedMaster:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if working_media.info.has_audio:
            filter_complex = "[0:v]setpts=PTS/%s[v];[0:a]volume=%sdB,alimiter=limit=0.93,atempo=%s[a]" % (
                self.config.speed_factor,
                self.config.preprocess_audio_gain_db,
                self.config.speed_factor,
            )
            command = [
                self.config.ffmpeg_bin,
                "-y",
                "-i",
                str(working_media.path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                str(self.config.target_sample_rate),
                str(output_path),
            ]
        else:
            command = [
                self.config.ffmpeg_bin,
                "-y",
                "-i",
                str(working_media.path),
                "-vf",
                "setpts=PTS/%s" % self.config.speed_factor,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        self.runner.run(command)
        output_info = self.probe.probe(output_path)
        return ProcessedMaster(path=output_path, info=output_info, speed_factor=self.config.speed_factor)
