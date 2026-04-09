"""Audio extraction before scene shuffle and finishing for the final cut."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import FinalAudioAsset, PreparedAudioAsset, ProcessedMaster
from auto_tiktok_editor.utils.command import CommandRunner


MEAN_RE = re.compile(r"mean_volume:\s*(?P<value>-?\d+(?:\.\d+)?) dB")
MAX_RE = re.compile(r"max_volume:\s*(?P<value>-?\d+(?:\.\d+)?) dB")


class AudioFinisher(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner

    def prepare(self, processed_master: ProcessedMaster, output_path: Path) -> PreparedAudioAsset:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        warnings = []
        if not processed_master.info.has_audio:
            warnings.append("Source audio was missing; a silent final audio track will be generated.")
            return PreparedAudioAsset(
                path=output_path,
                has_audio=False,
                duration_seconds=processed_master.info.duration_seconds,
                warnings=warnings,
            )

        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(processed_master.path),
            "-vn",
            "-c:a",
            "aac",
            "-ar",
            str(self.config.target_sample_rate),
            str(output_path),
        ]
        self.runner.run(command)
        return PreparedAudioAsset(
            path=output_path,
            has_audio=True,
            duration_seconds=processed_master.info.duration_seconds,
            warnings=warnings,
        )

    def finish(self, prepared_audio: PreparedAudioAsset, duration_seconds: float, output_path: Path) -> FinalAudioAsset:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        warnings = list(prepared_audio.warnings)
        if not prepared_audio.has_audio:
            self._render_silence(duration_seconds, output_path)
            return FinalAudioAsset(path=output_path, has_audio=False, warnings=warnings)

        mean_volume, max_volume = self._analyze(prepared_audio.path)
        if mean_volume is not None and mean_volume < -30.0:
            warnings.append("Source audio is very quiet; loudness normalization was applied conservatively.")
        if max_volume is not None and max_volume > self.config.audio_true_peak:
            warnings.append("Source audio is already near clipping; limiter protection was applied.")
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(prepared_audio.path),
            "-t",
            "%.3f" % max(0.1, duration_seconds),
            "-af",
            "loudnorm=I=%s:LRA=11:TP=%s,alimiter=limit=0.89" % (
                self.config.audio_target_lufs,
                self.config.audio_true_peak,
            ),
            "-c:a",
            "aac",
            "-ar",
            str(self.config.target_sample_rate),
            str(output_path),
        ]
        self.runner.run(command)
        return FinalAudioAsset(path=output_path, has_audio=True, warnings=warnings)

    def _render_silence(self, duration_seconds: float, output_path: Path) -> None:
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=%d" % self.config.target_sample_rate,
            "-t",
            "%.3f" % max(0.1, duration_seconds),
            "-c:a",
            "aac",
            str(output_path),
        ]
        self.runner.run(command)

    def _analyze(self, media_path: Path) -> Tuple[Optional[float], Optional[float]]:
        command = [
            self.config.ffmpeg_bin,
            "-i",
            str(media_path),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            self.runner.devnull,
        ]
        completed = self.runner.run(command, check=True, capture_output=True)
        stderr = completed.stderr or ""
        mean_match = MEAN_RE.search(stderr)
        max_match = MAX_RE.search(stderr)
        mean_volume = float(mean_match.group("value")) if mean_match else None
        max_volume = float(max_match.group("value")) if max_match else None
        return mean_volume, max_volume
