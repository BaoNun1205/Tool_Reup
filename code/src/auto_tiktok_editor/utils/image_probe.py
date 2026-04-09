"""Minimal image metadata parsing without third-party dependencies."""

from __future__ import annotations

from pathlib import Path
import imghdr
import struct
from typing import BinaryIO

from auto_tiktok_editor.domain.models import ImageInfo
from auto_tiktok_editor.exceptions import ValidationError


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def probe_image(path: Path) -> ImageInfo:
    image_type = imghdr.what(str(path))
    if image_type == "png":
        width, height, has_alpha = _read_png_info(path)
        return ImageInfo(
            path=path,
            width=width,
            height=height,
            mime_type="image/png",
            image_type="png",
            has_alpha=has_alpha,
        )
    if image_type in ("jpeg", "jpg"):
        width, height = _read_jpeg_info(path)
        return ImageInfo(
            path=path,
            width=width,
            height=height,
            mime_type="image/jpeg",
            image_type="jpg",
            has_alpha=False,
        )
    raise ValidationError("Product image must be PNG or JPG/JPEG.")


def _read_png_info(path: Path):
    with path.open("rb") as handle:
        if handle.read(8) != PNG_SIGNATURE:
            raise ValidationError("Invalid PNG signature.")
        has_alpha = False
        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                raise ValidationError("PNG file is truncated.")
            length, chunk_type = struct.unpack(">I4s", chunk_header)
            chunk_data = handle.read(length)
            handle.read(4)
            if chunk_type == b"IHDR":
                width, height, _bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
                has_alpha = color_type in (4, 6)
            elif chunk_type == b"tRNS":
                has_alpha = True
            elif chunk_type == b"IEND":
                break
        return width, height, has_alpha


def _read_jpeg_info(path: Path):
    with path.open("rb") as handle:
        if handle.read(2) != b"\xFF\xD8":
            raise ValidationError("Invalid JPEG signature.")
        while True:
            marker = _next_jpeg_marker(handle)
            if marker is None:
                break
            if marker in JPEG_SOF_MARKERS:
                segment_length = struct.unpack(">H", handle.read(2))[0]
                _precision = handle.read(1)
                height, width = struct.unpack(">HH", handle.read(4))
                return width, height
            segment_length = struct.unpack(">H", handle.read(2))[0]
            handle.seek(segment_length - 2, 1)
    raise ValidationError("Unable to read JPEG dimensions.")


def _next_jpeg_marker(handle: BinaryIO):
    while True:
        byte = handle.read(1)
        if not byte:
            return None
        if byte != b"\xFF":
            continue
        while byte == b"\xFF":
            byte = handle.read(1)
        if not byte:
            return None
        if byte != b"\x00":
            return ord(byte)
