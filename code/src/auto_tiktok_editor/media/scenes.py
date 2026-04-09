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
        black_ranges = self._detect_black_ranges(processed_master)
        warnings = []
        if len(raw_scenes) <= 1:
            warnings.append("Scene detector found very few cuts; output may stay close to the source order.")
        return raw_scenes, black_ranges, warnings

    def _detect_scene_boundaries(self, processed_master: ProcessedMaster) -> List[SceneRange]:
        command = [
            self.config.ffmpeg_bin,
            "-i",
            str(processed_master.path),
            "-filter:v",
            "select='gt(scene,%s)',showinfo" % self.config.scene_threshold,
            "-an",
            "-f",
            "null",
            self.runner.devnull,
        ]
        completed = self.runner.run(command, check=True, capture_output=True)
        cut_points = [0.0]
        for match in SHOWINFO_RE.finditer(completed.stderr or ""):
            timestamp = float(match.group("time"))
            if 0.0 < timestamp < processed_master.info.duration_seconds:
                cut_points.append(timestamp)
        unique_points = sorted(set(cut_points + [processed_master.info.duration_seconds]))
        if len(unique_points) < 2:
            unique_points = [0.0, processed_master.info.duration_seconds]
        scenes = []
        for index in range(len(unique_points) - 1):
            start = unique_points[index]
            end = unique_points[index + 1]
            scenes.append(
                SceneRange(
                    start_seconds=start,
                    end_seconds=end,
                    source_index=index,
                    origin_start_seconds=start,
                    origin_end_seconds=end,
                )
            )
        return scenes

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
