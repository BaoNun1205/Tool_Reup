"""Helpers for deleting generated media files from configured storage roots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
from typing import List, Optional, Set

from auto_tiktok_editor.config import PipelineConfig


MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".webm",
    ".wmv",
    ".flv",
    ".ts",
    ".m2ts",
    ".mts",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
}


@dataclass
class MediaCleanupReport:
    roots: List[Path] = field(default_factory=list)
    deleted_files: int = 0
    deleted_directories: int = 0
    freed_bytes: int = 0
    errors: List[str] = field(default_factory=list)


def cleanup_media_storage(
    config: PipelineConfig,
    older_than_seconds: Optional[int] = None,
) -> MediaCleanupReport:
    report = MediaCleanupReport(roots=_cleanup_roots(config))
    seen_files = set()  # type: Set[Path]
    cutoff_timestamp = None
    if older_than_seconds is not None:
        cutoff_timestamp = time.time() - max(0, int(older_than_seconds))
    for root in report.roots:
        if not root.exists() or not root.is_dir():
            continue
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in MEDIA_SUFFIXES:
                continue
            if cutoff_timestamp is not None:
                try:
                    if file_path.stat().st_mtime > cutoff_timestamp:
                        continue
                except OSError as exc:
                    report.errors.append("%s: %s" % (file_path, exc))
                    continue
            resolved_path = file_path.resolve()
            if resolved_path in seen_files:
                continue
            seen_files.add(resolved_path)
            try:
                report.freed_bytes += file_path.stat().st_size
            except OSError:
                pass
            try:
                file_path.unlink()
                report.deleted_files += 1
            except OSError as exc:
                report.errors.append("%s: %s" % (file_path, exc))
        report.deleted_directories += _remove_empty_directories(root, report.errors, cutoff_timestamp)
    return report


def cleanup_tool_storage(
    config: PipelineConfig,
    project_root: Path | str,
) -> MediaCleanupReport:
    report = MediaCleanupReport(roots=_tool_cleanup_roots(config, project_root))
    seen_roots = set()  # type: Set[Path]
    for root in report.roots:
        if not root.exists() or not root.is_dir():
            continue
        resolved_root = root.resolve()
        if resolved_root in seen_roots:
            continue
        seen_roots.add(resolved_root)
        _remove_root_contents(root, report)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            report.errors.append("%s: %s" % (root, exc))
    return report


def format_cleanup_report(report: MediaCleanupReport, *, include_errors: bool = True) -> str:
    if report.deleted_files == 0 and not report.errors:
        return "Khong tim thay video hoac anh nao trong input/output de xoa."
    parts = ["Da xoa %s file media" % report.deleted_files]
    if report.deleted_directories:
        parts.append("don %s thu muc rong" % report.deleted_directories)
    if report.freed_bytes > 0:
        parts.append("giai phong %s" % _human_size(report.freed_bytes))
    message = ", ".join(parts) + "."
    if include_errors and report.errors:
        message += " Co %s file khong xoa duoc." % len(report.errors)
    return message


def format_tool_cleanup_report(report: MediaCleanupReport, *, include_errors: bool = True) -> str:
    if report.deleted_files == 0 and report.deleted_directories == 0 and not report.errors:
        return "Khong tim thay du lieu tool nao de don dep."
    parts = ["Da xoa %s file" % report.deleted_files]
    if report.deleted_directories:
        parts.append("xoa %s thu muc" % report.deleted_directories)
    if report.freed_bytes > 0:
        parts.append("giai phong %s" % _human_size(report.freed_bytes))
    message = ", ".join(parts) + "."
    if include_errors and report.errors:
        message += " Co %s muc khong xoa duoc." % len(report.errors)
    return message


def _cleanup_roots(config: PipelineConfig) -> List[Path]:
    unique_roots = []
    seen = set()  # type: Set[Path]
    for root in (Path(config.telegram_input_root), Path(config.default_output_root)):
        resolved = root.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_roots.append(resolved)
    return unique_roots


def _tool_cleanup_roots(config: PipelineConfig, project_root: Path | str) -> List[Path]:
    root = Path(project_root).expanduser().resolve()
    candidates = [
        Path(config.default_output_root),
        Path(config.telegram_input_root),
        root / "profile_video_queue",
        root / "tmp",
        root / "phone_screenshots",
        root / "logs",
    ]
    unique_roots = []
    seen = set()  # type: Set[Path]
    protected_files = {
        root / "tiktok_profile_manager.sqlite3",
        root / "telegram_bots.json",
        root / "telegram_bots.example.json",
    }
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        if resolved in protected_files:
            continue
        seen.add(resolved)
        unique_roots.append(resolved)
    return unique_roots


def _remove_root_contents(root: Path, report: MediaCleanupReport) -> None:
    try:
        children = list(root.iterdir())
    except OSError as exc:
        report.errors.append("%s: %s" % (root, exc))
        return
    for child in children:
        if child.is_file() or child.is_symlink():
            _delete_file(child, report)
            continue
        if child.is_dir():
            files, directories, bytes_count = _tree_stats(child)
            try:
                shutil.rmtree(child)
                report.deleted_files += files
                report.deleted_directories += directories + 1
                report.freed_bytes += bytes_count
            except OSError as exc:
                report.errors.append("%s: %s" % (child, exc))


def _delete_file(path: Path, report: MediaCleanupReport) -> None:
    try:
        report.freed_bytes += path.stat().st_size
    except OSError:
        pass
    try:
        path.unlink()
        report.deleted_files += 1
    except OSError as exc:
        report.errors.append("%s: %s" % (path, exc))


def _tree_stats(root: Path) -> tuple[int, int, int]:
    files = 0
    directories = 0
    bytes_count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() or path.is_symlink():
                files += 1
                try:
                    bytes_count += path.stat().st_size
                except OSError:
                    pass
            elif path.is_dir():
                directories += 1
        except OSError:
            continue
    return files, directories, bytes_count


def _remove_empty_directories(root: Path, errors: List[str], cutoff_timestamp: Optional[float] = None) -> int:
    removed = 0
    directories = []
    try:
        candidates = root.rglob("*")
        for path in candidates:
            try:
                if not path.is_dir():
                    continue
                if cutoff_timestamp is not None and path.stat().st_mtime > cutoff_timestamp:
                    continue
                directories.append(path)
            except OSError as exc:
                errors.append("%s: %s" % (path, exc))
    except OSError as exc:
        errors.append("%s: %s" % (root, exc))
        return removed
    directories.sort(key=lambda current: len(current.parts), reverse=True)
    for directory in directories:
        try:
            next(directory.iterdir())
            continue
        except StopIteration:
            try:
                directory.rmdir()
                removed += 1
            except OSError as exc:
                errors.append("%s: %s" % (directory, exc))
        except OSError:
            continue
    return removed


def _human_size(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(max(0, int(size_bytes)))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "%d %s" % (int(size), unit)
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % int(size_bytes)
