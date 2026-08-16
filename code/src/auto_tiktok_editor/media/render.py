"""Rendering helpers for clip extraction, rough cut assembly, and final compositing."""

from __future__ import annotations

from pathlib import Path
import os
import shutil

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import EditPlan, FinalAudioAsset, OverlaySpec, ProcessedMaster, RoughCutAsset, SceneRange
from auto_tiktok_editor.exceptions import ExternalToolError
from auto_tiktok_editor.media.ffmpeg import video_encoder_args
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
            *video_encoder_args(self.config, crf=self.config.video_crf),
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
            *video_encoder_args(self.config, crf=self.config.video_crf),
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
            *video_encoder_args(self.config, final=True),
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.config.target_fps),
            "-b:v",
            self.config.final_video_bitrate,
            "-maxrate",
            self.config.final_video_maxrate,
            "-bufsize",
            self.config.final_video_bufsize,
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            "-b:a",
            self.config.final_audio_bitrate,
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
        image_zoom_peak_factor = max(0.1, getattr(self.config, "split_image_zoom_peak_factor", image_scale_factor))
        scaled_image_width = max(target_width, self._even_int(target_width * image_scale_factor))
        zoom_peak_image_width = max(
            target_width,
            self._even_int(target_width * image_zoom_peak_factor),
        )
        image_frame_height = overlay_height
        image_crop_aspect = self._image_crop_aspect(overlay_spec.image_crop_ratio)
        # Anh san pham duoc crop theo ti le da chon roi zoom ben trong khung co dinh
        # canh day; fade bat dau tai mep tren khung anh.
        base_frame_width = min(target_width, scaled_image_width)
        base_frame_height = min(image_frame_height, self._even_int(target_width / image_crop_aspect))
        separator_alpha_ratio = overlay_spec.separator_max_alpha_ratio
        if separator_alpha_ratio is None:
            separator_alpha_ratio = self.config.split_separator_max_alpha_ratio
        separator_fade_ratio = overlay_spec.separator_fade_ratio
        if separator_fade_ratio is None:
            separator_fade_ratio = self.config.split_separator_fade_ratio
        separator_fade_ratio = max(0.05, min(0.95, float(separator_fade_ratio)))
        fade_gamma = max(0.35, min(2.5, 2.2 - (separator_alpha_ratio * 1.8)))
        image_bg = self._ffmpeg_color(overlay_spec.image_background_color or self.config.split_image_background_color)
        crop_width_expr, crop_height_expr = self._source_crop_expressions(image_crop_aspect)
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
        motion_cycle_frames = max(
            1.0,
            self.config.target_fps * max(0.1, getattr(self.config, "split_image_motion_cycle_seconds", 6.0)),
        )
        horizontal_float_pixels = max(
            0,
            int(round(getattr(self.config, "split_image_horizontal_float_ratio", 0.018) * target_width)),
        )
        vertical_float_pixels = max(
            0,
            int(round(getattr(self.config, "split_image_vertical_float_ratio", 0.014) * base_frame_height)),
        )
        if self._image_motion(overlay_spec.image_motion) == "zoom":
            image_scale_expr = "%0.4f+%0.4f*(0.5-0.5*cos(2*PI*n/%0.4f))" % (
                scaled_image_width,
                image_scale_delta,
                zoom_cycle_frames,
            )
            image_crop_x_expr = "min(max(0\\,(iw-%d)/2+%d*sin(2*PI*n/%0.4f))\\,iw-%d)" % (
                base_frame_width,
                horizontal_float_pixels,
                motion_cycle_frames,
                base_frame_width,
            )
            image_crop_y_expr = "min(max(0\\,ih-%d-%d*(0.5-0.5*cos(2*PI*n/%0.4f)))\\,ih-%d)" % (
                base_frame_height,
                vertical_float_pixels,
                motion_cycle_frames,
                base_frame_height,
            )
        else:
            image_scale_expr = "%0.4f" % scaled_image_width
            image_crop_x_expr = "(iw-%d)/2" % base_frame_width
            image_crop_y_expr = "ih-%d" % base_frame_height
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
            "crop={base_frame_w}:{base_frame_h}:'{image_crop_x_expr}':'{image_crop_y_expr}'[imgcrop];"
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
            image_crop_x_expr=image_crop_x_expr,
            image_crop_y_expr=image_crop_y_expr,
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

    def _image_crop_aspect(self, value: str) -> float:
        normalized = str(value or "1:1").strip().lower().replace("x", ":")
        if normalized == "4:3":
            return 4.0 / 3.0
        return 1.0

    def _source_crop_expressions(self, aspect: float) -> tuple[str, str]:
        return (
            "if(gte(iw/ih\\,%0.4f)\\,ih*%0.4f\\,iw)" % (aspect, aspect),
            "if(gte(iw/ih\\,%0.4f)\\,ih\\,iw/%0.4f)" % (aspect, aspect),
        )

    def _image_motion(self, value: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"still", "zoom"} else "still"

    def _ffmpeg_color(self, value: str) -> str:
        return value.replace('#', '0x')


class RembgFrameProcessor(object):
    def process(
        self,
        input_frames_dir: Path,
        output_frames_dir: Path,
        model_name: str,
        providers: tuple,
        post_process_mask: bool,
        mask_expand_pixels: int,
    ) -> tuple:
        try:
            import onnxruntime as ort
            import numpy as np
            from PIL import Image, ImageFilter
            from rembg.bg import post_process
        except ImportError as exc:
            raise ExternalToolError(
                "rembg/onnxruntime-directml is not installed. Install rembg and onnxruntime-directml to use remove-background mode."
            ) from exc

        selected_providers = self._select_available_providers(ort, providers)
        session = self._new_rembg_session(ort, model_name, selected_providers)
        frame_paths = sorted(input_frames_dir.glob("frame_*.png"))
        if not frame_paths:
            raise ExternalToolError("No frames were extracted for rembg background removal.")

        output_frames_dir.mkdir(parents=True, exist_ok=True)
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                rgb_image = image.convert("RGB")
                masks = session.predict(rgb_image)
                if not masks:
                    raise ExternalToolError("rembg did not return a foreground mask for '%s'." % frame_path)
                mask = masks[0]
                if post_process_mask:
                    mask = Image.fromarray(post_process(np.array(mask)))
                mask = self._relax_mask(mask, mask_expand_pixels, ImageFilter)
                cutout = rgb_image.convert("RGBA")
                cutout.putalpha(mask)
                cutout.save(output_frames_dir / frame_path.name)
        return selected_providers

    def _relax_mask(self, mask, expand_pixels: int, image_filter):
        normalized = mask.convert("L")
        if expand_pixels <= 0:
            return normalized
        filter_size = (int(expand_pixels) * 2) + 1
        return normalized.filter(image_filter.MaxFilter(filter_size))

    def _select_available_providers(self, ort, configured_providers: tuple) -> tuple:
        available = set(ort.get_available_providers())
        selected = [provider for provider in configured_providers if provider in available]
        if "CPUExecutionProvider" in available and "CPUExecutionProvider" not in selected:
            selected.append("CPUExecutionProvider")
        if not selected:
            selected = ["CPUExecutionProvider"]
        return tuple(selected)

    def _new_rembg_session(self, ort, model_name: str, providers: tuple):
        try:
            from rembg.sessions import sessions_class

            session_class = None
            for candidate in sessions_class:
                if candidate.name() == model_name:
                    session_class = candidate
                    break
            if session_class is None:
                raise ValueError("No rembg session class found for model '%s'" % model_name)
            session_options = ort.SessionOptions()
            if "DmlExecutionProvider" in providers:
                session_options.enable_mem_pattern = False
                session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            if "OMP_NUM_THREADS" in os.environ:
                threads = int(os.environ["OMP_NUM_THREADS"])
                session_options.inter_op_num_threads = threads
                session_options.intra_op_num_threads = threads
            return session_class(model_name, session_options, providers=list(providers))
        except Exception as exc:
            raise ExternalToolError("Unable to create rembg session for model '%s': %s" % (model_name, exc)) from exc


class BackgroundRemovalCompositor(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner, rembg_processor=None):
        self.config = config
        self.runner = runner
        self.rembg_processor = rembg_processor or RembgFrameProcessor()

    def compose(
        self,
        rough_cut: RoughCutAsset,
        final_audio: FinalAudioAsset,
        product_image_path: Path,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._backend_name() == "backgroundremover":
            return self._compose_with_backgroundremover(
                rough_cut,
                final_audio,
                product_image_path,
                output_path,
            )
        return self._compose_with_rembg(
            rough_cut,
            final_audio,
            product_image_path,
            output_path,
        )

    def _compose_with_backgroundremover(
        self,
        rough_cut: RoughCutAsset,
        final_audio: FinalAudioAsset,
        product_image_path: Path,
        output_path: Path,
    ) -> Path:
        subject_path = output_path.with_name("%s_subject_alpha.mov" % output_path.stem)
        self._remove_video_background(rough_cut.path, subject_path)
        self._compose_subject_over_product_background(
            subject_path,
            product_image_path,
            final_audio,
            rough_cut.info.duration_seconds,
            output_path,
        )
        return output_path

    def _compose_with_rembg(
        self,
        rough_cut: RoughCutAsset,
        final_audio: FinalAudioAsset,
        product_image_path: Path,
        output_path: Path,
    ) -> Path:
        work_dir = output_path.with_name("%s_rembg_frames" % output_path.stem)
        raw_frames_dir = work_dir / "raw"
        cutout_frames_dir = work_dir / "cutout"
        try:
            self._reset_directory(raw_frames_dir)
            self._reset_directory(cutout_frames_dir)
            self._extract_video_frames(rough_cut.path, raw_frames_dir)
            self.rembg_processor.process(
                raw_frames_dir,
                cutout_frames_dir,
                self._rembg_model_name(),
                self._rembg_providers(),
                self._rembg_post_process_mask(),
                self._rembg_mask_expand_pixels(),
            )
            self._compose_rembg_frames_over_product_background(
                cutout_frames_dir,
                product_image_path,
                final_audio,
                rough_cut.info.duration_seconds,
                output_path,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return output_path

    def _extract_video_frames(self, input_path: Path, output_frames_dir: Path) -> None:
        filter_complex = (
            "fps=%d,scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1"
            % (
                self.config.target_fps,
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
            )
        )
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-vf",
            filter_complex,
            "-an",
            str(output_frames_dir / "frame_%06d.png"),
        ]
        self.runner.run(command)

    def _remove_video_background(self, input_path: Path, output_path: Path) -> None:
        command = [
            self.config.backgroundremover_bin,
            "-i",
            str(input_path),
            "--model",
            self._model_name(),
            "-tv",
            "-o",
            str(output_path),
        ]
        self.runner.run(command)

    def _compose_rembg_frames_over_product_background(
        self,
        cutout_frames_dir: Path,
        product_image_path: Path,
        final_audio: FinalAudioAsset,
        duration_seconds: float,
        output_path: Path,
    ) -> None:
        filter_complex = (
            "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1,format=rgba[bg];"
            "[1:v]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1,format=rgba[fg];"
            "[bg][fg]overlay=0:0:shortest=1:format=auto,format=yuv420p[vout]"
            % (
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
            )
        )
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-i",
            str(product_image_path),
            "-framerate",
            str(self.config.target_fps),
            "-i",
            str(cutout_frames_dir / "frame_%06d.png"),
            "-i",
            str(final_audio.path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "2:a:0",
            *video_encoder_args(self.config, final=True),
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.config.target_fps),
            "-b:v",
            self.config.final_video_bitrate,
            "-maxrate",
            self.config.final_video_maxrate,
            "-bufsize",
            self.config.final_video_bufsize,
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            "-b:a",
            self.config.final_audio_bitrate,
            "-ar",
            str(self.config.target_sample_rate),
            "-t",
            format_seconds(duration_seconds),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self.runner.run(command)

    def _compose_subject_over_product_background(
        self,
        subject_path: Path,
        product_image_path: Path,
        final_audio: FinalAudioAsset,
        duration_seconds: float,
        output_path: Path,
    ) -> None:
        filter_complex = (
            "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1,format=rgba[bg];"
            "[1:v]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1,format=rgba[fg];"
            "[bg][fg]overlay=0:0:shortest=1:format=auto,format=yuv420p[vout]"
            % (
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
                self.config.target_width,
                self.config.target_height,
            )
        )
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-i",
            str(product_image_path),
            "-i",
            str(subject_path),
            "-i",
            str(final_audio.path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "2:a:0",
            *video_encoder_args(self.config, final=True),
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.config.target_fps),
            "-b:v",
            self.config.final_video_bitrate,
            "-maxrate",
            self.config.final_video_maxrate,
            "-bufsize",
            self.config.final_video_bufsize,
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            "-b:a",
            self.config.final_audio_bitrate,
            "-ar",
            str(self.config.target_sample_rate),
            "-t",
            format_seconds(duration_seconds),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self.runner.run(command)

    def _reset_directory(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)

    def _backend_name(self) -> str:
        backend = str(getattr(self.config, "background_removal_backend", "rembg") or "rembg").strip().lower()
        return backend if backend in {"rembg", "backgroundremover"} else "rembg"

    def _rembg_model_name(self) -> str:
        model_name = str(getattr(self.config, "rembg_model", "isnet-general-use") or "isnet-general-use").strip().lower()
        allowed = {
            "u2net",
            "u2netp",
            "u2net-human-seg",
            "u2net-cloth-seg",
            "silueta",
            "isnet-general-use",
            "isnet-anime",
            "birefnet-general",
        }
        return model_name if model_name in allowed else "isnet-general-use"

    def _rembg_providers(self) -> tuple:
        providers = getattr(self.config, "rembg_providers", ("DmlExecutionProvider", "CPUExecutionProvider"))
        if isinstance(providers, str):
            providers = tuple(chunk.strip() for chunk in providers.split(",") if chunk.strip())
        return tuple(providers or ("DmlExecutionProvider", "CPUExecutionProvider"))

    def _rembg_post_process_mask(self) -> bool:
        return bool(getattr(self.config, "rembg_post_process_mask", False))

    def _rembg_mask_expand_pixels(self) -> int:
        return max(0, int(getattr(self.config, "rembg_mask_expand_pixels", 3) or 0))

    def _model_name(self) -> str:
        model_name = str(getattr(self.config, "backgroundremover_model", "u2netp") or "u2netp").strip().lower()
        return model_name if model_name in {"u2net", "u2net_human_seg", "u2netp"} else "u2netp"
