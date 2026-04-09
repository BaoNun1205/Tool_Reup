"""Media probing via ffprobe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import MediaInfo
from auto_tiktok_editor.exceptions import ProbeError
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.utils.timecode import parse_fraction


class MediaProbe(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner

    def probe(self, media_path: Path) -> MediaInfo:
        command = [
            self.config.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(media_path),
        ]
        completed = self.runner.run(command)
        try:
            payload = json.loads(completed.stdout or "{}")
        except ValueError as exc:
            raise ProbeError("ffprobe returned invalid JSON.") from exc
        return self._parse_media_info(media_path, payload)

    def _parse_media_info(self, media_path: Path, payload: Dict[str, Any]) -> MediaInfo:
        streams = payload.get("streams", [])
        format_info = payload.get("format", {})
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if not video_stream:
            raise ProbeError("Media file does not contain a valid video stream.")
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration_value = format_info.get("duration") or video_stream.get("duration") or 0
        frame_rate = parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0")
        try:
            duration_seconds = float(duration_value)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        audio_sample_rate = None
        if audio_stream and audio_stream.get("sample_rate"):
            try:
                audio_sample_rate = int(audio_stream.get("sample_rate"))
            except (TypeError, ValueError):
                audio_sample_rate = None
        return MediaInfo(
            path=media_path,
            duration_seconds=duration_seconds,
            width=int(video_stream.get("width") or 0),
            height=int(video_stream.get("height") or 0),
            frame_rate=frame_rate,
            has_audio=audio_stream is not None,
            audio_sample_rate=audio_sample_rate,
            video_codec=video_stream.get("codec_name"),
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        )
