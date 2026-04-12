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
        image_trim_top = max(0, min(overlay_height - 2, int(round(overlay_height * self.config.split_image_trim_top_ratio))))
        visible_image_height = max(2, overlay_height - image_trim_top)
        mask_blur = max(12, int(round(feather_height / 16.0)))
        separator_alpha_ratio = overlay_spec.separator_max_alpha_ratio
        if separator_alpha_ratio is None:
            separator_alpha_ratio = self.config.split_separator_max_alpha_ratio
        separator_max_alpha = max(32, min(255, int(round(255.0 * separator_alpha_ratio))))
        zoom_factor = overlay_spec.zoom_factor or self.config.split_zoom_factor
        image_bg = self._ffmpeg_color(overlay_spec.image_background_color or self.config.split_image_background_color)
        zoom_expr = "%0.4f" % zoom_factor
        square_expr = "min(iw\,ih)"
        mask_expr = "if(lte(Y\,%d)\,%d*Y/%d\,255)" % (feather_height, separator_max_alpha, feather_height)
        return (
            "[0:v]scale={tw}:-2:flags=lanczos,setsar=1,pad={tw}:{th}:0:0:color=black[basev];"
            "[2:v]format=rgba,crop='{square}':'{square}':(iw-{square})/2:(ih-{square})/2,"
            "scale={tw}:{overlay_h}:force_original_aspect_ratio=increase,"
            "scale=trunc(iw*{zoom}/2)*2:trunc(ih*{zoom}/2)*2,crop={tw}:{visible_h}:(iw-{tw})/2:ih-{visible_h}[imgcrop];"
            "color=color={bg}:size={tw}x{overlay_h},format=rgba[imgbg];"
            "[imgbg][imgcrop]overlay=0:{image_y}:shortest=1:eof_action=pass,format=rgba[bottomsrc];"
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
            visible_h=visible_image_height,
            image_y=image_trim_top,
            square=square_expr,
            zoom=zoom_expr,
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
