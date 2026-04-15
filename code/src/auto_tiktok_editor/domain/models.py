"""Domain models and pipeline/session contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Dict, List, Optional


@dataclass
class JobSpec:
    source_video_url: str
    product_image: Path
    output_dir: Path
    output_basename: Optional[str] = None
    shuffle_seed: Optional[int] = None
    cookies_file: Optional[Path] = None
    overlay_alpha_ratio: Optional[float] = None


@dataclass
class SessionItemSpec:
    row_id: str
    source_video_url: str = ""
    product_image: Optional[Path] = None
    output_basename: Optional[str] = None
    shuffle_seed: Optional[int] = None
    overlay_alpha_ratio: Optional[float] = None


@dataclass
class SessionSpec:
    items: List[SessionItemSpec]
    output_root_dir: Path
    session_name: Optional[str] = None
    cookies_file: Optional[Path] = None


@dataclass
class ImageInfo:
    path: Path
    width: int
    height: int
    mime_type: str
    image_type: str
    has_alpha: bool

    @property
    def longest_edge(self) -> int:
        return max(self.width, self.height)


@dataclass
class ValidatedJob:
    job_spec: JobSpec
    image_info: ImageInfo
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidatedSessionItem:
    item_index: int
    row_id: str
    item_spec: SessionItemSpec
    validated_job: ValidatedJob
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidatedSession:
    session_spec: SessionSpec
    items: List[ValidatedSessionItem]
    warnings: List[str] = field(default_factory=list)


@dataclass
class SourceAsset:
    source_url: str
    downloaded_path: Path
    extractor_name: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaInfo:
    path: Path
    duration_seconds: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool
    audio_sample_rate: Optional[int]
    video_codec: Optional[str]
    audio_codec: Optional[str]


@dataclass
class WorkingMedia:
    path: Path
    info: MediaInfo
    warnings: List[str] = field(default_factory=list)


@dataclass
class ProcessedMaster:
    path: Path
    info: MediaInfo
    speed_factor: float


@dataclass
class SceneRange:
    start_seconds: float
    end_seconds: float
    source_index: int
    origin_start_seconds: Optional[float] = None
    origin_end_seconds: Optional[float] = None
    drop_reason: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass
class EditPlan:
    seed: int
    opener_index: Optional[int]
    closer_index: Optional[int]
    ordered_scenes: List[SceneRange]
    dropped_scenes: List[SceneRange] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class OverlaySpec:
    source_image_path: Path
    image_type: str
    mode: str
    x: int
    y: int
    content_width: int
    content_height: int
    panel_width: int
    panel_height: int
    padding: int
    shadow_offset: int
    warnings: List[str] = field(default_factory=list)
    video_panel_height: int = 0
    image_panel_height: int = 0
    separator_height: int = 0
    zoom_factor: float = 1.0
    video_trim_bottom_ratio: float = 0.0
    image_background_color: str = "#FFF4C2"
    separator_max_alpha_ratio: Optional[float] = None


@dataclass
class RoughCutAsset:
    path: Path
    info: MediaInfo
    clip_paths: List[Path]


@dataclass
class PreparedAudioAsset:
    path: Path
    has_audio: bool
    duration_seconds: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class FinalAudioAsset:
    path: Path
    has_audio: bool
    warnings: List[str] = field(default_factory=list)


@dataclass
class JobArtifacts:
    output_dir: Path
    final_video_path: Optional[Path]
    final_audio_path: Optional[Path]
    video_title_path: Optional[Path]
    metadata_path: Optional[Path]
    process_log_path: Optional[Path]


@dataclass
class ItemProcessResult:
    item_index: int
    row_id: str
    job_id: str
    status: str
    source_video_url: str
    product_image_path: Optional[Path]
    output_dir: Path
    artifacts: JobArtifacts
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class SessionArtifacts:
    session_dir: Optional[Path]
    summary_path: Optional[Path]
    session_log_path: Optional[Path]
    titles_path: Optional[Path] = None
    is_finalized: bool = False


@dataclass
class SessionResult:
    session_id: str
    status: str
    items: List[ItemProcessResult]
    warnings: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    artifacts: Optional[SessionArtifacts] = None
    row_errors: Dict[int, List[str]] = field(default_factory=dict)


@dataclass
class SessionEvent:
    event_type: str
    session_id: Optional[str] = None
    item_index: Optional[int] = None
    row_id: Optional[str] = None
    stage: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
