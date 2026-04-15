"""Overlay planning for product image compositing."""

from __future__ import annotations

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ImageInfo, OverlaySpec


class OverlayPlanner(object):
    def __init__(self, config: PipelineConfig):
        self.config = config

    def plan(self, image_info: ImageInfo, separator_max_alpha_ratio: float | None = None) -> OverlaySpec:
        bottom_panel_height = int(round(self.config.target_height * self.config.split_bottom_panel_ratio))
        separator_height = int(round(self.config.target_height * self.config.split_separator_height_ratio))
        overlay_height = bottom_panel_height + separator_height
        overlay_y = self.config.target_height - overlay_height
        warnings = []
        if image_info.has_alpha:
            warnings.append("PNG transparency will be flattened against a warm background in the split layout.")
        return OverlaySpec(
            source_image_path=image_info.path,
            image_type=image_info.image_type,
            mode="stacked_split_mask",
            x=0,
            y=overlay_y,
            content_width=self.config.target_width,
            content_height=overlay_height,
            panel_width=self.config.target_width,
            panel_height=overlay_height,
            padding=0,
            shadow_offset=0,
            warnings=warnings,
            video_panel_height=self.config.target_height,
            image_panel_height=bottom_panel_height,
            separator_height=separator_height,
            zoom_factor=self.config.split_image_scale_factor,
            video_trim_bottom_ratio=self.config.split_video_trim_bottom_ratio,
            image_background_color=self.config.split_image_background_color,
            separator_max_alpha_ratio=separator_max_alpha_ratio,
        )
