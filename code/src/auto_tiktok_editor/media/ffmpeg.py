"""Small helpers for FFmpeg command construction."""

from __future__ import annotations


def video_encoder_args(config, crf=None, final: bool = False) -> list[str]:
    encoder = str(getattr(config, "video_encoder", "h264_amf") or "h264_amf").strip()
    if encoder == "libx264":
        args = ["-c:v", "libx264", "-preset", "medium"]
        if crf is not None:
            args.extend(["-crf", str(crf)])
        return args
    if encoder == "h264_amf":
        args = ["-c:v", "h264_amf"]
        if not final:
            args.extend(
                [
                    "-b:v",
                    str(getattr(config, "amf_intermediate_bitrate", "30M") or "30M"),
                    "-maxrate",
                    str(getattr(config, "amf_intermediate_maxrate", "40M") or "40M"),
                    "-bufsize",
                    str(getattr(config, "amf_intermediate_bufsize", "60M") or "60M"),
                ]
            )
        return args
    return ["-c:v", encoder]
