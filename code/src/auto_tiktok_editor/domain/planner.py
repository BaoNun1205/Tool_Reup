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
        cleaned = []
        for scene in raw_scenes:
            if scene.duration_seconds <= 0.05:
                scene.drop_reason = "degenerate"
                dropped.append(scene)
                continue
            if self._black_overlap_ratio(scene, black_ranges) >= 0.8:
                scene.drop_reason = "mostly_black"
                dropped.append(scene)
                continue
            cleaned.append(copy.deepcopy(scene))

        merged = []
        for scene in cleaned:
            if not merged:
                merged.append(scene)
                continue
            if merged[-1].duration_seconds < self.config.min_scene_duration:
                merged[-1].end_seconds = scene.end_seconds
                merged[-1].origin_end_seconds = scene.origin_end_seconds or scene.end_seconds
            else:
                merged.append(scene)

        usable = []
        for scene in merged:
            if scene.duration_seconds < self.config.min_scene_duration:
                if usable:
                    usable[-1].end_seconds = scene.end_seconds
                    usable[-1].origin_end_seconds = scene.origin_end_seconds or scene.end_seconds
                else:
                    scene.drop_reason = "too_short"
                    dropped.append(scene)
                continue
            usable.extend(self._split_scene(scene))

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
        if len(scenes) <= 2:
            chosen_seed = seed if seed is not None else 0
            return EditPlan(
                seed=chosen_seed,
                opener_index=scenes[0].source_index,
                closer_index=scenes[-1].source_index,
                ordered_scenes=list(scenes),
                warnings=["Scene count is too low for meaningful shuffle; original order was kept."],
            )

        chosen_seed = seed if seed is not None else random.SystemRandom().randint(1, 10 ** 9)
        opener_pool_size = max(1, int(math.ceil(len(scenes) * 0.2)))
        closer_pool_size = opener_pool_size
        opener = self._pick_longest(scenes[:opener_pool_size])
        closer_candidates = [scene for scene in scenes[-closer_pool_size:] if scene is not opener]
        closer = self._pick_longest(closer_candidates or [scenes[-1]])
        middle = [scene for scene in scenes if scene not in (opener, closer)]
        rng = random.Random(chosen_seed)
        rng.shuffle(middle)
        ordered = [opener] + self._repair_adjacency(middle, opener, closer) + [closer]
        warnings = []
        if self._contains_hard_adjacency(ordered):
            warnings.append("Some adjacent source regions remained after constrained shuffle repair.")
        return EditPlan(
            seed=chosen_seed,
            opener_index=opener.source_index,
            closer_index=closer.source_index,
            ordered_scenes=ordered,
            warnings=warnings,
        )

    def _pick_longest(self, scenes: Sequence[SceneRange]) -> SceneRange:
        return max(scenes, key=lambda scene: scene.duration_seconds)

    def _repair_adjacency(
        self,
        scenes: List[SceneRange],
        opener: SceneRange,
        closer: SceneRange,
    ) -> List[SceneRange]:
        ordered = list(scenes)
        for index in range(len(ordered)):
            prev_scene = opener if index == 0 else ordered[index - 1]
            next_scene = closer if index == len(ordered) - 1 else ordered[index + 1]
            if not self._is_bad_neighbor(prev_scene, ordered[index]) and not self._is_bad_neighbor(ordered[index], next_scene):
                continue
            swap_index = self._find_swap_candidate(ordered, index, prev_scene, next_scene)
            if swap_index is not None:
                ordered[index], ordered[swap_index] = ordered[swap_index], ordered[index]
        return ordered

    def _find_swap_candidate(
        self,
        scenes: List[SceneRange],
        index: int,
        prev_scene: SceneRange,
        next_scene: SceneRange,
    ) -> Optional[int]:
        for candidate_index in range(index + 1, len(scenes)):
            candidate = scenes[candidate_index]
            candidate_prev = prev_scene
            candidate_next = next_scene if candidate_index == len(scenes) - 1 else scenes[candidate_index + 1]
            if self._is_bad_neighbor(candidate_prev, candidate):
                continue
            if self._is_bad_neighbor(candidate, next_scene):
                continue
            if index > 0 and self._is_bad_neighbor(scenes[candidate_index - 1], scenes[index]):
                continue
            if candidate_index < len(scenes) - 1 and self._is_bad_neighbor(scenes[index], candidate_next):
                continue
            return candidate_index
        return None

    def _contains_hard_adjacency(self, ordered: Sequence[SceneRange]) -> bool:
        for left, right in zip(ordered, ordered[1:]):
            if self._is_bad_neighbor(left, right):
                return True
        return False

    def _is_bad_neighbor(self, left: SceneRange, right: SceneRange) -> bool:
        if abs(left.source_index - right.source_index) <= 1:
            return True
        if left.duration_seconds and right.duration_seconds:
            larger = max(left.duration_seconds, right.duration_seconds)
            smaller = min(left.duration_seconds, right.duration_seconds)
            if smaller > 0 and larger / smaller > 3.0:
                return True
        return False
