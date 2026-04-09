"""Workspace management for session runs and per-item pipeline jobs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class SessionWorkspace:
    root_dir: Path
    items_dir: Path


@dataclass
class ItemWorkspace:
    root_dir: Path
    source_dir: Path
    normalized_dir: Path
    processed_dir: Path
    clips_dir: Path
    output_dir: Path


def create_session_workspace(output_root_dir: Path, session_id: str) -> SessionWorkspace:
    root_dir = output_root_dir / session_id
    items_dir = root_dir / "items"
    for directory in (root_dir, items_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return SessionWorkspace(root_dir=root_dir, items_dir=items_dir)


def create_item_workspace(session_workspace: SessionWorkspace, item_index: int, row_id: str) -> ItemWorkspace:
    safe_row_id = _slugify(row_id) or "item"
    root_dir = session_workspace.items_dir / ("item_%03d_%s" % (item_index + 1, safe_row_id))
    source_dir = root_dir / "source"
    normalized_dir = root_dir / "normalized"
    processed_dir = root_dir / "processed"
    clips_dir = root_dir / "clips"
    output_dir = root_dir
    for directory in (root_dir, source_dir, normalized_dir, processed_dir, clips_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return ItemWorkspace(
        root_dir=root_dir,
        source_dir=source_dir,
        normalized_dir=normalized_dir,
        processed_dir=processed_dir,
        clips_dir=clips_dir,
        output_dir=output_dir,
    )


def _slugify(value: str) -> str:
    return SAFE_SEGMENT_RE.sub("_", (value or "").strip()).strip("_")
