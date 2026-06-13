"""Scene detection helpers based on ffmpeg scene and blackdetect filters."""

from __future__ import annotations

import os
import re
from typing import List, Sequence, Tuple

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ProcessedMaster, SceneRange
from auto_tiktok_editor.utils.command import CommandRunner


SHOWINFO_RE = re.compile(r"pts_time:(?P<time>\d+(?:\.\d+)?)")
BLACK_RE = re.compile(
    r"black_start:(?P<start>\d+(?:\.\d+)?)\s+black_end:(?P<end>\d+(?:\.\d+)?)"
)


class SceneDetector(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner

    def detect(self, processed_master: ProcessedMaster):
        raw_scenes = self._detect_scene_boundaries(processed_master)
        mode = str(getattr(self.config, "video_cut_mode", "fixed") or "fixed").strip().lower()
        if mode == "original":
            return raw_scenes, [], []
        black_ranges = self._detect_black_ranges(processed_master)
        warnings = []
        if len(raw_scenes) <= 1:
            warnings.append("Scene detector found very few cuts; output may stay close to the source order.")
        return raw_scenes, black_ranges, warnings

    def _detect_scene_boundaries(self, processed_master: ProcessedMaster) -> List[SceneRange]:
        mode = str(getattr(self.config, "video_cut_mode", "fixed") or "fixed").strip().lower()
        if mode == "original":
            return self._detect_original_boundaries(processed_master)
        if mode == "scene":
            return self._detect_natural_scene_boundaries(processed_master)
        return self._detect_fixed_chunk_boundaries(processed_master)

    def _detect_original_boundaries(self, processed_master: ProcessedMaster) -> List[SceneRange]:
        total_duration = max(0.0, processed_master.info.duration_seconds)
        return [
            SceneRange(
                start_seconds=0.0,
                end_seconds=total_duration,
                source_index=0,
                origin_start_seconds=0.0,
                origin_end_seconds=total_duration,
            )
        ]

    def _detect_fixed_chunk_boundaries(self, processed_master: ProcessedMaster) -> List[SceneRange]:
        chunk_duration = max(0.5, float(self.config.fixed_chunk_duration_seconds))
        total_duration = max(0.0, processed_master.info.duration_seconds)
        if total_duration <= 0.0:
            return [
                SceneRange(
                    start_seconds=0.0,
                    end_seconds=0.0,
                    source_index=0,
                    origin_start_seconds=0.0,
                    origin_end_seconds=0.0,
                )
            ]

        scenes = []
        cursor = 0.0
        index = 0
        while cursor < total_duration - 0.001:
            end = min(total_duration, cursor + chunk_duration)
            scenes.append(
                SceneRange(
                    start_seconds=cursor,
                    end_seconds=end,
                    source_index=index,
                    origin_start_seconds=cursor,
                    origin_end_seconds=end,
                )
            )
            cursor = end
            index += 1
        return scenes

    def _detect_natural_scene_boundaries(self, processed_master: ProcessedMaster) -> List[SceneRange]:
        total_duration = max(0.0, processed_master.info.duration_seconds)
        if total_duration <= 0.0:
            return [
                SceneRange(
                    start_seconds=0.0,
                    end_seconds=0.0,
                    source_index=0,
                    origin_start_seconds=0.0,
                    origin_end_seconds=0.0,
                )
            ]

        threshold = max(0.01, min(0.95, float(self.config.scene_threshold)))
        command = [
            self.config.ffmpeg_bin,
            "-i",
            str(processed_master.path),
            "-vf",
            "select=gt(scene\\,%s),showinfo" % threshold,
            "-an",
            "-f",
            "null",
            self.runner.devnull,
        ]
        completed = self.runner.run(command, check=True, capture_output=True)
        boundaries = []
        seen = set()
        for match in SHOWINFO_RE.finditer(completed.stderr or ""):
            timestamp = round(float(match.group("time")), 3)
            if timestamp <= 0.05 or timestamp >= total_duration - 0.05:
                continue
            if timestamp in seen:
                continue
            seen.add(timestamp)
            boundaries.append(timestamp)
        return self._build_ranges_from_boundaries(total_duration, sorted(boundaries))

    def _build_ranges_from_boundaries(self, total_duration: float, boundaries: Sequence[float]) -> List[SceneRange]:
        points = [0.0]
        points.extend(boundaries)
        points.append(total_duration)
        scenes = []
        for index, (start, end) in enumerate(zip(points, points[1:])):
            if end - start <= 0.001:
                continue
            scenes.append(
                SceneRange(
                    start_seconds=start,
                    end_seconds=end,
                    source_index=index,
                    origin_start_seconds=start,
                    origin_end_seconds=end,
                )
            )
        if scenes:
            return scenes
        return [
            SceneRange(
                start_seconds=0.0,
                end_seconds=total_duration,
                source_index=0,
                origin_start_seconds=0.0,
                origin_end_seconds=total_duration,
            )
        ]

    def _detect_black_ranges(self, processed_master: ProcessedMaster) -> List[Tuple[float, float]]:
        command = [
            self.config.ffmpeg_bin,
            "-i",
            str(processed_master.path),
            "-vf",
            "blackdetect=d=%s:pic_th=%s" % (
                self.config.blackdetect_duration,
                self.config.blackdetect_threshold,
            ),
            "-an",
            "-f",
            "null",
            self.runner.devnull,
        ]
        completed = self.runner.run(command, check=True, capture_output=True)
        ranges = []
        for match in BLACK_RE.finditer(completed.stderr or ""):
            ranges.append((float(match.group("start")), float(match.group("end"))))
        return ranges
