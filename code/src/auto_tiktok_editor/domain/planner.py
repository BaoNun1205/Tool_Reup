"""Scene qualification and constrained shuffle planning."""

from __future__ import annotations

import copy
import math
import random
from typing import List, Optional, Sequence, Tuple

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import EditPlan, SceneRange
from auto_tiktok_editor.exceptions import PipelineStageError


class SceneQualifier(object):
    def __init__(self, config: PipelineConfig):
        self.config = config

    def qualify(
        self,
        raw_scenes: Sequence[SceneRange],
        black_ranges: Sequence[Tuple[float, float]],
    ) -> Tuple[List[SceneRange], List[SceneRange], List[str]]:
        dropped = []
        usable = []
        for scene in raw_scenes:
            if scene.duration_seconds <= 0.05:
                scene.drop_reason = "degenerate"
                dropped.append(scene)
                continue
            if self._black_overlap_ratio(scene, black_ranges) >= 0.8:
                scene.drop_reason = "mostly_black"
                dropped.append(scene)
                continue
            usable.extend(self._split_scene(copy.deepcopy(scene)))

        warnings = []
        if len(usable) < 3:
            warnings.append("Low scene variety detected; shuffle effect may be limited.")
        if not usable:
            raise PipelineStageError("No usable scenes were produced after qualification.")
        return usable, dropped, warnings

    def _split_scene(self, scene: SceneRange) -> List[SceneRange]:
        if scene.duration_seconds <= self.config.max_scene_duration:
            return [scene]
        pieces = []
        total_duration = scene.duration_seconds
        parts = int(math.ceil(total_duration / self.config.max_scene_duration))
        chunk_duration = total_duration / float(parts)
        cursor = scene.start_seconds
        for index in range(parts):
            end = scene.end_seconds if index == parts - 1 else cursor + chunk_duration
            pieces.append(
                SceneRange(
                    start_seconds=cursor,
                    end_seconds=end,
                    source_index=scene.source_index,
                    origin_start_seconds=cursor,
                    origin_end_seconds=end,
                )
            )
            cursor = end
        if len(pieces) > 1 and pieces[-1].duration_seconds < self.config.min_scene_duration:
            pieces[-2].end_seconds = pieces[-1].end_seconds
            pieces.pop()
        return pieces

    def _black_overlap_ratio(
        self,
        scene: SceneRange,
        black_ranges: Sequence[Tuple[float, float]],
    ) -> float:
        if not black_ranges or scene.duration_seconds <= 0:
            return 0.0
        overlap = 0.0
        for start, end in black_ranges:
            overlap += max(0.0, min(scene.end_seconds, end) - max(scene.start_seconds, start))
        return overlap / scene.duration_seconds


class EditPlanner(object):
    def __init__(self, config: PipelineConfig):
        self.config = config

    def build(self, scenes: Sequence[SceneRange], seed: Optional[int] = None) -> EditPlan:
        if not scenes:
            raise PipelineStageError("Edit planner received no scenes.")
        chosen_seed = seed if seed is not None else random.SystemRandom().randint(1, 10 ** 9)
        ordered = self._build_strict_shuffle(list(scenes), random.Random(chosen_seed))
        warnings = []
        if self._violates_shuffle_constraints(ordered):
            warnings.append(
                "Scene count is too low for a perfect shuffle; some chunks may still remain close to their source neighbors."
            )
        return EditPlan(
            seed=chosen_seed,
            opener_index=ordered[0].source_index if ordered else None,
            closer_index=ordered[-1].source_index if ordered else None,
            ordered_scenes=ordered,
            dropped_scenes=[],
            warnings=warnings,
        )

    def _build_strict_shuffle(self, scenes: List[SceneRange], rng: random.Random) -> List[SceneRange]:
        if len(scenes) <= 1:
            return list(scenes)
        if len(scenes) == 2:
            return [scenes[1], scenes[0]]

        best_order = list(scenes)
        best_score = self._score_order(best_order)
        max_attempts = max(64, min(1024, len(scenes) * 32))
        for _attempt in range(max_attempts):
            candidate = self._build_backtracking_order(list(scenes), rng)
            if candidate is None:
                shuffled = list(scenes)
                rng.shuffle(shuffled)
                candidate = shuffled
            score = self._score_order(candidate)
            if score < best_score:
                best_order = candidate
                best_score = score
            if score == (0, 0):
                return candidate
        return best_order

    def _build_backtracking_order(
        self,
        scenes: List[SceneRange],
        rng: random.Random,
    ) -> Optional[List[SceneRange]]:
        ordered = []  # type: List[SceneRange]
        remaining = list(scenes)

        def backtrack(position: int) -> bool:
            if not remaining:
                return True
            candidate_indexes = list(range(len(remaining)))
            rng.shuffle(candidate_indexes)
            candidate_indexes.sort(key=lambda idx: self._candidate_rank(remaining[idx], position, ordered[-1] if ordered else None))
            for candidate_index in candidate_indexes:
                candidate = remaining[candidate_index]
                if candidate.source_index == position:
                    continue
                if ordered and self._is_bad_neighbor(ordered[-1], candidate):
                    continue
                ordered.append(candidate)
                remaining.pop(candidate_index)
                if backtrack(position + 1):
                    return True
                remaining.insert(candidate_index, candidate)
                ordered.pop()
            return False

        if backtrack(0):
            return ordered
        return None

    def _candidate_rank(
        self,
        candidate: SceneRange,
        position: int,
        prev_scene: Optional[SceneRange],
    ) -> Tuple[int, int, float]:
        fixed_position_penalty = 0 if candidate.source_index != position else 1
        neighbor_penalty = 0 if prev_scene is None or not self._is_bad_neighbor(prev_scene, candidate) else 1
        distance_score = abs(candidate.source_index - position)
        return (fixed_position_penalty, neighbor_penalty, -float(distance_score))

    def _score_order(self, ordered: List[SceneRange]) -> Tuple[int, int]:
        if not ordered:
            return (0, 0)
        fixed_position_count = 0
        hard_adjacency_count = 0
        for index, scene in enumerate(ordered):
            if scene.source_index == index:
                fixed_position_count += 1
        for left, right in zip(ordered, ordered[1:]):
            if self._is_bad_neighbor(left, right):
                hard_adjacency_count += 1
        return (hard_adjacency_count, fixed_position_count)

    def _violates_shuffle_constraints(self, ordered: Sequence[SceneRange]) -> bool:
        if any(scene.source_index == index for index, scene in enumerate(ordered)):
            return True
        for left, right in zip(ordered, ordered[1:]):
            if self._is_bad_neighbor(left, right):
                return True
        return False

    def _is_bad_neighbor(self, left: SceneRange, right: SceneRange) -> bool:
        return abs(left.source_index - right.source_index) <= 1
