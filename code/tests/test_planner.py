import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import unittest
from unittest import mock

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ImageInfo, SceneRange
from auto_tiktok_editor.domain.planner import EditPlanner, SceneQualifier
from auto_tiktok_editor.exceptions import PipelineStageError
from auto_tiktok_editor.media.overlay import OverlayPlanner


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.config = PipelineConfig()
        self.qualifier = SceneQualifier(self.config)
        self.planner = EditPlanner(self.config)
        self.overlay_planner = OverlayPlanner(self.config)

    def test_qualifier_splits_long_scenes_below_three_seconds_without_merging_back(self):
        raw_scenes = [
            SceneRange(0.0, 0.4, 0),
            SceneRange(0.4, 1.6, 1),
            SceneRange(1.6, 7.0, 2),
        ]
        usable, dropped, warnings = self.qualifier.qualify(raw_scenes, black_ranges=[])
        self.assertGreaterEqual(len(usable), 4)
        self.assertTrue(all(scene.duration_seconds < 3.0 for scene in usable))
        self.assertGreater(len([scene for scene in usable if scene.source_index == 2]), 1)
        self.assertAlmostEqual(usable[-1].end_seconds, 7.0)
        self.assertEqual(dropped, [])

    def test_qualifier_drops_mostly_black_scene(self):
        raw_scenes = [
            SceneRange(0.0, 1.2, 0),
            SceneRange(1.2, 2.4, 1),
            SceneRange(2.4, 3.6, 2),
        ]
        usable, dropped, warnings = self.qualifier.qualify(raw_scenes, black_ranges=[(1.2, 2.4)])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].drop_reason, "mostly_black")
        self.assertEqual(len(usable), 2)

    def test_edit_planner_is_deterministic_for_seed(self):
        scenes = [
            SceneRange(0.0, 1.1, 0),
            SceneRange(1.1, 2.3, 3),
            SceneRange(2.3, 3.5, 6),
            SceneRange(3.5, 4.7, 9),
            SceneRange(4.7, 6.0, 12),
        ]
        plan_a = self.planner.build(scenes, seed=123)
        plan_b = self.planner.build(scenes, seed=123)
        self.assertEqual(
            [scene.source_index for scene in plan_a.ordered_scenes],
            [scene.source_index for scene in plan_b.ordered_scenes],
        )
        self.assertEqual(len(plan_a.dropped_scenes), len(plan_b.dropped_scenes))
        self.assertCountEqual([scene.source_index for scene in plan_a.ordered_scenes], [scene.source_index for scene in scenes])
        for index, scene in enumerate(plan_a.ordered_scenes):
            self.assertNotEqual(scene.source_index, index)
        for left, right in zip(plan_a.ordered_scenes, plan_a.ordered_scenes[1:]):
            self.assertGreater(abs(left.source_index - right.source_index), 1)

    def test_edit_planner_avoids_same_source_adjacency_when_possible(self):
        scenes = [
            SceneRange(0.0, 1.0, 0),
            SceneRange(1.0, 2.0, 2),
            SceneRange(2.0, 3.0, 2),
            SceneRange(3.0, 4.0, 5),
            SceneRange(4.0, 5.0, 7),
        ]
        plan = self.planner.build(scenes, seed=123)
        ordered_sources = [scene.source_index for scene in plan.ordered_scenes]
        for left, right in zip(ordered_sources, ordered_sources[1:]):
            self.assertGreater(abs(left - right), 1)
        for index, source in enumerate(ordered_sources):
            self.assertNotEqual(source, index)

    def test_edit_planner_warns_when_constraints_cannot_be_resolved_for_tiny_inputs(self):
        scenes = [
            SceneRange(0.0, 1.0, 0),
            SceneRange(1.0, 2.0, 1),
            SceneRange(2.0, 3.0, 2),
        ]
        plan = self.planner.build(scenes, seed=123)

        self.assertEqual(plan.dropped_scenes, [])
        self.assertTrue(plan.warnings)

    def test_edit_planner_times_out_instead_of_hanging(self):
        scenes = [
            SceneRange(0.0, 1.0, 0),
            SceneRange(1.0, 2.0, 1),
            SceneRange(2.0, 3.0, 2),
        ]
        with mock.patch("auto_tiktok_editor.domain.planner.time.monotonic", side_effect=[0.0, 301.0]):
            with self.assertRaises(PipelineStageError) as raised:
                self.planner.build(scenes, seed=123)
        self.assertIn("timed out after 300 seconds", str(raised.exception))

    def test_overlay_planner_uses_stacked_split_layout(self):
        spec = self.overlay_planner.plan(
            ImageInfo(
                path=Path('product.png'),
                width=1200,
                height=900,
                mime_type='image/png',
                image_type='png',
                has_alpha=True,
            )
        )
        self.assertEqual(spec.mode, 'stacked_split_mask')
        self.assertEqual(spec.panel_width, self.config.target_width)
        self.assertEqual(spec.video_panel_height, self.config.target_height)
        self.assertEqual(spec.image_panel_height, int(round(self.config.target_height * self.config.split_bottom_panel_ratio)))
        self.assertGreater(spec.separator_height, 0)
        self.assertEqual(spec.panel_height, spec.image_panel_height + spec.separator_height)
        self.assertEqual(spec.y, self.config.target_height - spec.panel_height)
        self.assertEqual(spec.separator_height, int(round(self.config.target_height * self.config.split_separator_height_ratio)))
        self.assertAlmostEqual(spec.zoom_factor, self.config.split_image_scale_factor)
        self.assertAlmostEqual(spec.video_trim_bottom_ratio, self.config.split_video_trim_bottom_ratio)


if __name__ == "__main__":
    unittest.main()
