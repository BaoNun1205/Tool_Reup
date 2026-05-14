"""Rendering helpers for clip extraction, rough cut assembly, and final compositing."""

from __future__ import annotations

from pathlib import Path

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import EditPlan, FinalAudioAsset, OverlaySpec, ProcessedMaster, RoughCutAsset, SceneRange
from auto_tiktok_editor.media.probe import MediaProbe
from auto_tiktok_editor.utils.command import CommandRunner
from auto_tiktok_editor.utils.timecode import format_seconds


class RoughCutRenderer(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner, probe: MediaProbe):
        self.config = config
        self.runner = runner
        self.probe = probe

    def render(
        self,
        processed_master: ProcessedMaster,
        edit_plan: EditPlan,
        clips_dir: Path,
        output_path: Path,
    ) -> RoughCutAsset:
        clips_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        clip_paths = []
        for index, scene in enumerate(edit_plan.ordered_scenes):
            clip_path = clips_dir / ("clip_%03d.mp4" % index)
            self._render_clip(processed_master, scene, clip_path)
            clip_paths.append(clip_path)
        concat_list = clips_dir / "clips.txt"
        concat_lines = []
        for path in clip_paths:
            concat_lines.append("file '%s'" % path.resolve().as_posix())
        concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(self.config.video_crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.config.target_fps),
        ]
        if processed_master.info.has_audio:
            command.extend(["-c:a", "aac", "-ar", str(self.config.target_sample_rate)])
        else:
            command.append("-an")
        command.append(str(output_path))
        self.runner.run(command)
        info = self.probe.probe(output_path)
        return RoughCutAsset(path=output_path, info=info, clip_paths=clip_paths)

    def _render_clip(self, processed_master: ProcessedMaster, scene: SceneRange, output_path: Path) -> None:
        fade = min(self.config.temp_audio_fade_seconds, max(0.01, scene.duration_seconds / 4.0))
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-ss",
            format_seconds(scene.start_seconds),
            "-t",
            format_seconds(scene.duration_seconds),
            "-i",
            str(processed_master.path),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(self.config.video_crf),
            "-pix_fmt",
            "yuv420p",
        ]
        if processed_master.info.has_audio:
            out_fade_start = max(0.0, scene.duration_seconds - fade)
            af = "afade=t=in:st=0:d=%s,afade=t=out:st=%s:d=%s,aresample=%d" % (
                format_seconds(fade),
                format_seconds(out_fade_start),
                format_seconds(fade),
                self.config.target_sample_rate,
            )
            command.extend(["-af", af, "-c:a", "aac", "-ar", str(self.config.target_sample_rate)])
        else:
            command.append("-an")
        command.append(str(output_path))
        self.runner.run(command)


class FinalCompositor(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner

    def compose(
        self,
        rough_cut: RoughCutAsset,
        final_audio: FinalAudioAsset,
        overlay_spec: OverlaySpec,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filter_complex = self._build_filter_complex(overlay_spec)
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(rough_cut.path),
            "-i",
            str(final_audio.path),
            "-loop",
            "1",
            "-i",
            str(overlay_spec.source_image_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            str(self.config.target_sample_rate),
            "-t",
            format_seconds(rough_cut.info.duration_seconds),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self.runner.run(command)
        return output_path

    def _build_filter_complex(self, overlay_spec: OverlaySpec) -> str:
        if overlay_spec.mode == "stacked_split_mask":
            return self._append_final_aspect_enforcement(self._build_split_filter_complex(overlay_spec))
        if overlay_spec.mode == "png_alpha_feather":
            return self._append_final_aspect_enforcement(
                (
                    "[2:v]scale={w}:{h},format=rgba,split=2[imgbase][imgalpha];"
                    "[imgalpha]alphaextract,boxblur=3:1[alphafeather];"
                    "[imgbase][alphafeather]alphamerge[img];"
                    "[img]alphaextract,boxblur=12:1[shadowmask];"
                    "color=color=black@0.30:size={w}x{h},format=rgba[shadowbase];"
                    "[shadowbase][shadowmask]alphamerge[shadow];"
                    "[0:v][shadow]overlay={sx}:{sy}:shortest=1:eof_action=pass[v1];"
                    "[v1][img]overlay={x}:{y}:shortest=1:eof_action=pass[vraw]"
                ).format(
                    w=overlay_spec.content_width,
                    h=overlay_spec.content_height,
                    x=overlay_spec.x,
                    y=overlay_spec.y,
                    sx=overlay_spec.x + overlay_spec.shadow_offset,
                    sy=overlay_spec.y + overlay_spec.shadow_offset,
                )
            )
        return self._append_final_aspect_enforcement(
            (
                "color=color=black@0.20:size={pw}x{ph},format=rgba,boxblur=18:1[panelshadow];"
                "color=color=white@0.18:size={pw}x{ph},format=rgba,boxblur=6:1[panel];"
                "[2:v]scale={w}:{h},format=rgba[img];"
                "[0:v][panelshadow]overlay={sx}:{sy}:shortest=1:eof_action=pass[v1];"
                "[v1][panel]overlay={x}:{y}:shortest=1:eof_action=pass[v2];"
                "[v2][img]overlay={ix}:{iy}:shortest=1:eof_action=pass[vraw]"
            ).format(
                pw=overlay_spec.panel_width,
                ph=overlay_spec.panel_height,
                w=overlay_spec.content_width,
                h=overlay_spec.content_height,
                x=overlay_spec.x,
                y=overlay_spec.y,
                sx=overlay_spec.x + overlay_spec.shadow_offset,
                sy=overlay_spec.y + overlay_spec.shadow_offset,
                ix=overlay_spec.x + overlay_spec.padding,
                iy=overlay_spec.y + overlay_spec.padding,
            )
        )

    def _build_split_filter_complex(self, overlay_spec: OverlaySpec) -> str:
        target_width = self.config.target_width
        target_height = self.config.target_height
        bottom_height = overlay_spec.image_panel_height or int(round(target_height * self.config.split_bottom_panel_ratio))
        feather_height = overlay_spec.separator_height or int(round(target_height * self.config.split_separator_height_ratio))
        feather_height = max(24, feather_height)
        overlay_height = min(target_height, bottom_height + feather_height)
        overlay_y = max(0, min(target_height - overlay_height, overlay_spec.y))
        image_scale_factor = max(0.1, overlay_spec.zoom_factor or self.config.split_image_scale_factor)
        image_zoom_peak_factor = max(
            image_scale_factor,
            getattr(self.config, "split_image_zoom_peak_factor", image_scale_factor),
        )
        scaled_image_width = max(target_width, self._even_int(target_width * image_scale_factor))
        zoom_peak_image_width = max(
            scaled_image_width,
            target_width,
            self._even_int(target_width * image_zoom_peak_factor),
        )
        scaled_image_height = self._even_int(scaled_image_width * 0.75)
        image_frame_height = overlay_height
        # Anh san pham duoc crop 4:3 roi zoom ben trong khung co dinh
        # canh day; fade bat dau tai mep tren khung anh.
        base_frame_width = min(target_width, scaled_image_width)
        base_frame_height = min(image_frame_height, scaled_image_height)
        separator_alpha_ratio = overlay_spec.separator_max_alpha_ratio
        if separator_alpha_ratio is None:
            separator_alpha_ratio = self.config.split_separator_max_alpha_ratio
        separator_fade_ratio = overlay_spec.separator_fade_ratio
        if separator_fade_ratio is None:
            separator_fade_ratio = self.config.split_separator_fade_ratio
        separator_fade_ratio = max(0.05, min(0.95, float(separator_fade_ratio)))
        fade_gamma = max(0.35, min(2.5, 2.2 - (separator_alpha_ratio * 1.8)))
        image_bg = self._ffmpeg_color(overlay_spec.image_background_color or self.config.split_image_background_color)
        crop_width_expr = "min(iw\\,ih*4/3)"
        crop_height_expr = "min(ih\\,iw*3/4)"
        fade_height_target = max(
            24,
            int(round(base_frame_height * separator_fade_ratio))
            - max(0, int(getattr(self.config, "split_separator_fade_trim_pixels", 0))),
        )
        fade_start_y = max(0, overlay_height - base_frame_height)
        fade_end_y = min(overlay_height, fade_start_y + fade_height_target)
        fade_height = max(1, fade_end_y - fade_start_y)
        mask_blur = max(12, int(round(fade_height / 16.0)))
        zoom_cycle_frames = max(
            1.0,
            self.config.target_fps * max(0.1, getattr(self.config, "split_image_zoom_cycle_seconds", 6.0)),
        )
        image_scale_delta = zoom_peak_image_width - scaled_image_width
        image_scale_expr = "%0.4f+%0.4f*(1-abs(2*mod(n\\,%0.4f)/%0.4f-1))" % (
            scaled_image_width,
            image_scale_delta,
            zoom_cycle_frames,
            zoom_cycle_frames,
        )
        mask_expr = (
            "if(lte(Y\\,%d)\\,0\\,"
            "if(lte(Y\\,%d)\\,255*pow((Y-%d)/%d\\,%0.4f)\\,255))"
        ) % (
            fade_start_y,
            fade_end_y,
            fade_start_y,
            fade_height,
            fade_gamma,
        )
        return (
            "[0:v]scale={tw}:-2:flags=lanczos,setsar=1,pad={tw}:{th}:0:0:color=black[basev];"
            "[2:v]format=rgba,crop='{crop_w}':'{crop_h}':(iw-{crop_w})/2:(ih-{crop_h})/2,"
            "scale=w='{image_scale_expr}':h=-2:flags=lanczos:eval=frame,"
            "crop={base_frame_w}:{base_frame_h}:(iw-{base_frame_w})/2:ih-{base_frame_h}[imgcrop];"
            "color=color={bg}:size={tw}x{overlay_h},format=rgba[imgbg];"
            "[imgbg][imgcrop]overlay=0:H-h:shortest=1:eof_action=pass,format=rgba[bottomsrc];"
            "nullsrc=size={tw}x{overlay_h},format=gray,geq=lum='{mask}'[maskraw];"
            "[maskraw]boxblur={mask_blur}:1[bottommask];"
            "[bottomsrc][bottommask]alphamerge[bottom];"
            "[basev][bottom]overlay=0:{overlay_y}:shortest=1:eof_action=pass[vraw]"
        ).format(
            bg=image_bg,
            tw=target_width,
            th=target_height,
            overlay_h=overlay_height,
            overlay_y=overlay_y,
            base_frame_w=base_frame_width,
            base_frame_h=base_frame_height,
            image_scale_expr=image_scale_expr,
            crop_w=crop_width_expr,
            crop_h=crop_height_expr,
            mask=mask_expr,
            mask_blur=mask_blur,
        )

    def _append_final_aspect_enforcement(self, filter_body: str) -> str:
        return (
            filter_body
            + ";[vraw]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1[vout]"
            % (
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
            )
        )

    def _even_int(self, value: float) -> int:
        rounded = int(round(value))
        if rounded % 2 != 0:
            rounded += 1
        return rounded

    def _ffmpeg_color(self, value: str) -> str:
        return value.replace('#', '0x')
