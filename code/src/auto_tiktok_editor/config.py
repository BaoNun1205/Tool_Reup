"""Runtime configuration for the MVP pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import time
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_SCRIPTS = PROJECT_ROOT / ".venv" / "Scripts"


def _find_project_binary(executable_name):
    candidate = VENV_SCRIPTS / executable_name
    if candidate.exists():
        return str(candidate)
    return None


def _find_winget_binary(patterns, executable_name):
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return None
    base = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not base.exists():
        return None
    matches = []
    for pattern in patterns:
        matches.extend(sorted(base.glob(pattern)))
    for package_dir in reversed(matches):
        candidate = package_dir / executable_name
        if candidate.exists():
            return str(candidate)
        nested_matches = list(package_dir.rglob(executable_name))
        if nested_matches:
            return str(nested_matches[0])
    return None


def _resolve_tool(env_var, default_name, project_executable, winget_patterns, executable_name):
    configured = os.getenv(env_var)
    if configured:
        return configured
    project_binary = _find_project_binary(project_executable)
    if project_binary:
        return project_binary
    discovered = shutil.which(default_name)
    if discovered:
        return discovered
    winget_binary = _find_winget_binary(winget_patterns, executable_name)
    if winget_binary:
        return winget_binary
    return default_name


@dataclass(frozen=True)
class PipelineConfig:
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ytdlp_bin: str = "yt-dlp"
    default_output_root: Path = PROJECT_ROOT / "output"
    max_session_items: int = 20
    ui_poll_interval_ms: int = 120
    target_width: int = 1080
    target_height: int = 1920
    target_fps: int = 30
    target_sample_rate: int = 48000
    preprocess_audio_gain_db: float = 4.0
    speed_factor: float = 1.2
    scene_threshold: float = 0.35
    min_scene_duration: float = 0.9
    max_scene_duration: float = 2.95
    blackdetect_duration: float = 0.4
    blackdetect_threshold: float = 0.98
    overlay_margin: int = 48
    png_overlay_width_ratio: float = 0.28
    jpg_overlay_width_ratio: float = 0.26
    overlay_max_height_ratio: float = 0.26
    split_bottom_panel_ratio: float = 0.34
    split_separator_height_ratio: float = 0.18
    split_zoom_factor: float = 1.1
    split_video_trim_bottom_ratio: float = 0.20
    split_video_vertical_offset_ratio: float = 0.60
    split_image_background_color: str = "#FFF4C2"
    browser_cookie_freshness_seconds: int = 1800
    tiktok_web_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )
    audio_target_lufs: float = -14.0
    audio_true_peak: float = -1.0
    temp_audio_fade_seconds: float = 0.04

    @classmethod
    def from_env(cls):
        configured_output_root = os.getenv("AUTO_EDITOR_OUTPUT_ROOT")
        output_root = Path(configured_output_root).expanduser().resolve() if configured_output_root else PROJECT_ROOT / "output"
        return cls(
            ffmpeg_bin=_resolve_tool(
                "AUTO_EDITOR_FFMPEG_BIN",
                "ffmpeg",
                "ffmpeg.exe",
                ["Gyan.FFmpeg*", "yt-dlp.FFmpeg*"],
                "ffmpeg.exe",
            ),
            ffprobe_bin=_resolve_tool(
                "AUTO_EDITOR_FFPROBE_BIN",
                "ffprobe",
                "ffprobe.exe",
                ["Gyan.FFmpeg*", "yt-dlp.FFmpeg*"],
                "ffprobe.exe",
            ),
            ytdlp_bin=_resolve_tool(
                "AUTO_EDITOR_YTDLP_BIN",
                "yt-dlp",
                "yt-dlp.exe",
                ["yt-dlp.yt-dlp*"],
                "yt-dlp.exe",
            ),
            default_output_root=output_root,
        )

    def build_job_id(self):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:8])

    def build_session_id(self):
        timestamp = time.strftime("session_%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:6])
