"""Helpers for deleting generated media files, temporary caches, and system storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

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

# Final renders use these containers.  Keep this separate from MEDIA_SUFFIXES:
# a lightweight cleanup must never remove product images or source artifacts.
OUTPUT_VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".webm",
    ".wmv",
    ".flv",
}

BROWSER_CACHE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "CacheStorage",
    "ScriptCache",
    "ShaderCache",
    "GrShaderCache",
    "Crashpad",
    "blob_storage",
}

PROTECTED_PROFILE_NAMES = {
    "Cookies",
    "Cookies-journal",
    "Local Storage",
    "IndexedDB",
    "Preferences",
    "Login Data",
    "Login Data-journal",
    "Web Data",
    "Web Data-journal",
    "History",
    "History-journal",
    "Network",
}


@dataclass
class MediaCleanupReport:
    roots: List[Path] = field(default_factory=list)
    deleted_files: int = 0
    deleted_directories: int = 0
    freed_bytes: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class CleanupItemInfo:
    key: str
    group: str  # "safe", "media", "dev", "device"
    title: str
    description: str
    file_count: int = 0
    size_bytes: int = 0
    default_checked: bool = False
    warning_note: str = ""


@dataclass
class GranularCleanupReport:
    deleted_files: int = 0
    deleted_directories: int = 0
    freed_bytes: int = 0
    deleted_items: List[str] = field(default_factory=list)
    stale_videos_removed: int = 0
    logs_cleared: int = 0
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


def _calc_dir_stats(path: Path) -> Tuple[int, int]:
    if not path.exists():
        return 0, 0
    count = 0
    size = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() or f.is_symlink():
                count += 1
                size += f.stat().st_size
        except OSError:
            pass
    return count, size


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_final_output_video(path: Path) -> bool:
    """Return whether a path is a final rendered video, not an input artifact."""
    return path.suffix.lower() in OUTPUT_VIDEO_SUFFIXES and path.stem.lower().endswith("final_video")


def _iter_final_output_video_paths(
    config: PipelineConfig,
    project_root: Path,
    manager: Optional[object] = None,
):
    """Yield final renders only, without touching image/link inputs or video records."""
    output_root = Path(config.default_output_root).expanduser().resolve()
    queue_root = (project_root / "profile_video_queue").resolve()
    seen: Set[Path] = set()

    def _yield_if_valid(candidate: Path):
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            return
        if (
            resolved in seen
            or not resolved.is_file()
            or resolved.suffix.lower() not in OUTPUT_VIDEO_SUFFIXES
            or not (_is_within(resolved, output_root) or _is_within(resolved, queue_root))
        ):
            return
        seen.add(resolved)
        yield resolved

    # Render sessions are kept under output_root.  Only their explicitly named
    # final deliverables are safe to remove without affecting a future render.
    if output_root.exists() and output_root.is_dir():
        for candidate in output_root.rglob("*"):
            try:
                relative_parts = candidate.relative_to(output_root).parts
            except ValueError:
                continue
            if "_telegram_inputs" in relative_parts or not _is_final_output_video(candidate):
                continue
            yield from _yield_if_valid(candidate)

    # A rendered profile video is copied to the queue and its database record
    # points at that file.  Remove that file only; leave the record (URL/image)
    # intact so the "Tạo lại video" action continues to work.
    if manager and hasattr(manager, "list_videos") and hasattr(manager, "resolve_video_path"):
        try:
            videos = manager.list_videos()
        except Exception:
            videos = []
        for video in videos:
            try:
                candidate = Path(manager.resolve_video_path(video))
            except Exception:
                continue
            yield from _yield_if_valid(candidate)


def scan_cleanup_items(
    config: PipelineConfig,
    project_root: Path | str,
    phone_controller: Optional[object] = None,
    manager: Optional[object] = None,
) -> List[CleanupItemInfo]:
    """Scan storage and return detailed item info with real-time sizes and file counts."""
    root = Path(project_root).expanduser().resolve()
    items: List[CleanupItemInfo] = []

    # 1. Tmp & Render Cache
    tmp_dirs = [root / "tmp", root / "_tmp_tests", root / "clips"]
    tmp_files, tmp_size = 0, 0
    for td in tmp_dirs:
        c, s = _calc_dir_stats(td)
        tmp_files += c
        tmp_size += s
    items.append(
        CleanupItemInfo(
            key="tmp",
            group="safe",
            title="Tệp tạm & Cache render",
            description="Tệp tạm FFmpeg, audio và phân đoạn clip trong quá trình render.",
            file_count=tmp_files,
            size_bytes=tmp_size,
            default_checked=True,
        )
    )

    # 2. Phone Screenshots
    sc_files, sc_size = _calc_dir_stats(root / "phone_screenshots")
    items.append(
        CleanupItemInfo(
            key="phone_screenshots",
            group="safe",
            title="Ảnh chụp màn hình Phone",
            description="Ảnh chụp màn hình từ điện thoại Android thông qua kết nối ADB.",
            file_count=sc_files,
            size_bytes=sc_size,
            default_checked=True,
        )
    )

    # 3. System & Database Logs
    log_files, log_size = _calc_dir_stats(root / "logs")
    items.append(
        CleanupItemInfo(
            key="logs",
            group="safe",
            title="Nhật ký hệ thống (Logs)",
            description="Các file log trên ổ đĩa (.log) và lịch sử nhật ký trong cơ sở dữ liệu.",
            file_count=log_files,
            size_bytes=log_size,
            default_checked=True,
        )
    )

    # 4. Telegram Inputs
    try:
        tg_in = Path(config.telegram_input_root).expanduser().resolve()
        if not tg_in.exists():
            tg_in = root / "output" / "_telegram_inputs"
    except Exception:
        tg_in = root / "output" / "_telegram_inputs"
    tg_files, tg_size = _calc_dir_stats(tg_in)
    items.append(
        CleanupItemInfo(
            key="telegram_inputs",
            group="safe",
            title="File Input thô Telegram",
            description="Ảnh sản phẩm và video thô tải về từ bot Telegram.",
            file_count=tg_files,
            size_bytes=tg_size,
            default_checked=True,
        )
    )

    # 5. Browser Profiles Cache
    prof_root = root / "profiles"
    bc_files, bc_size = 0, 0
    if prof_root.exists():
        for f in prof_root.rglob("*"):
            try:
                if f.is_file():
                    parts = set(f.parts)
                    if parts & BROWSER_CACHE_DIR_NAMES and not (parts & PROTECTED_PROFILE_NAMES):
                        bc_files += 1
                        bc_size += f.stat().st_size
            except OSError:
                pass
    items.append(
        CleanupItemInfo(
            key="browser_cache",
            group="safe",
            title="Cache rác trình duyệt Profiles",
            description="Bộ nhớ đệm Chrome/Playwright (Giữ nguyên Cookie & Đăng nhập TikTok).",
            file_count=bc_files,
            size_bytes=bc_size,
            default_checked=True,
        )
    )

    # 6. Video Queue
    vq_files, vq_size = _calc_dir_stats(root / "profile_video_queue")
    items.append(
        CleanupItemInfo(
            key="video_queue",
            group="media",
            title="Hàng đợi Video Profile",
            description="Các video đang chờ xuất bản hoặc theo lịch đăng của từng Profile.",
            file_count=vq_files,
            size_bytes=vq_size,
            default_checked=False,
            warning_note="Xóa các video chờ đăng trong hàng đợi",
        )
    )

    # 7. Output Videos (Thành phẩm)
    out_root = Path(config.default_output_root).expanduser().resolve()
    out_files, out_size = 0, 0
    if out_root.exists():
        for session_dir in out_root.iterdir():
            if session_dir.is_dir() and session_dir.name != "_telegram_inputs":
                c, s = _calc_dir_stats(session_dir)
                out_files += c
                out_size += s
            elif session_dir.is_file():
                try:
                    out_files += 1
                    out_size += session_dir.stat().st_size
                except OSError:
                    pass
    items.append(
        CleanupItemInfo(
            key="output_videos",
            group="media",
            title="Toàn bộ Video thành phẩm Output",
            description="Tất cả các video đã render thành phẩm hoàn chỉnh trong thư mục output.",
            file_count=out_files,
            size_bytes=out_size,
            default_checked=False,
            warning_note="Xóa các video thành phẩm đã render",
        )
    )

    # 8. Final output videos only.  This intentionally preserves input images,
    # URL/link marker files, session folders, and video records so a video can
    # be rendered again with another mode.
    final_output_paths = list(_iter_final_output_video_paths(config, root, manager))
    final_output_size = sum(_file_size(path) for path in final_output_paths)
    items.append(
        CleanupItemInfo(
            key="output_video_files_only",
            group="media",
            title="Chỉ xóa video Output (giữ ảnh & link)",
            description="Chỉ xóa file video thành phẩm; giữ nguyên ảnh sản phẩm, link nguồn, thư mục và dữ liệu để có thể tạo lại ở mode khác.",
            file_count=len(final_output_paths),
            size_bytes=final_output_size,
            default_checked=False,
            warning_note="Video sẽ cần render lại trước khi gửi/đăng",
        )
    )

    # 9. Build Dir
    build_dir = root / "build"
    b_files, b_size = _calc_dir_stats(build_dir)
    if build_dir.exists() and b_files > 0:
        items.append(
            CleanupItemInfo(
                key="build_dir",
                group="dev",
                title="Thư mục Build Nuitka/PyInstaller",
                description="Các file tạm, object và binary từ các lần đóng gói ứng dụng trước đây.",
                file_count=b_files,
                size_bytes=b_size,
                default_checked=False,
            )
        )

    # 10. Legacy Backups
    bk_files, bk_size = 0, 0
    for bk in root.glob("data_backup_before_rebuild_*"):
        if bk.is_dir():
            c, s = _calc_dir_stats(bk)
            bk_files += c
            bk_size += s
    if bk_files > 0:
        items.append(
            CleanupItemInfo(
                key="legacy_backups",
                group="dev",
                title="Các thư mục Sao lưu cũ (Backup)",
                description="Dữ liệu sao lưu từ các đợt tái cấu trúc trước đây.",
                file_count=bk_files,
                size_bytes=bk_size,
                default_checked=False,
            )
        )

    return items


def execute_granular_cleanup(
    selected_keys: Sequence[str],
    config: PipelineConfig,
    project_root: Path | str,
    manager: Optional[object] = None,
    phone_controller: Optional[object] = None,
) -> GranularCleanupReport:
    """Execute cleanup for only the selected item keys with full protection for critical files."""
    root = Path(project_root).expanduser().resolve()
    report = GranularCleanupReport()
    keys_set = set(selected_keys)

    # 1. Tmp & Render Cache
    if "tmp" in keys_set:
        for td in (root / "tmp", root / "_tmp_tests", root / "clips"):
            if td.exists() and td.is_dir():
                _remove_dir_children(td, report)
                td.mkdir(parents=True, exist_ok=True)
        report.deleted_items.append("tmp")

    # 2. Phone Screenshots
    if "phone_screenshots" in keys_set:
        sc_dir = root / "phone_screenshots"
        if sc_dir.exists() and sc_dir.is_dir():
            _remove_dir_children(sc_dir, report)
            sc_dir.mkdir(parents=True, exist_ok=True)
        report.deleted_items.append("phone_screenshots")

    # 3. System Logs & Database Logs
    if "logs" in keys_set:
        log_dir = root / "logs"
        if log_dir.exists() and log_dir.is_dir():
            _remove_dir_children(log_dir, report)
            log_dir.mkdir(parents=True, exist_ok=True)
        if manager and hasattr(manager, "clear_logs"):
            try:
                cleared_count = manager.clear_logs()
                report.logs_cleared += int(cleared_count or 0)
            except Exception as exc:
                report.errors.append("Clear DB logs: %s" % exc)
        report.deleted_items.append("logs")

    # 4. Telegram Inputs
    if "telegram_inputs" in keys_set:
        try:
            tg_in = Path(config.telegram_input_root).expanduser().resolve()
            if not tg_in.exists():
                tg_in = root / "output" / "_telegram_inputs"
        except Exception:
            tg_in = root / "output" / "_telegram_inputs"
        if tg_in.exists() and tg_in.is_dir():
            _remove_dir_children(tg_in, report)
            tg_in.mkdir(parents=True, exist_ok=True)
        report.deleted_items.append("telegram_inputs")

    # 5. Browser Profiles Cache (Safe Cache Only)
    if "browser_cache" in keys_set:
        prof_root = root / "profiles"
        if prof_root.exists() and prof_root.is_dir():
            for cache_dir_candidate in prof_root.rglob("*"):
                try:
                    if not cache_dir_candidate.is_dir():
                        continue
                    if cache_dir_candidate.name in BROWSER_CACHE_DIR_NAMES:
                        # Double check that no protected folder is inside or parent of this target
                        parts = set(cache_dir_candidate.parts)
                        if not (parts & PROTECTED_PROFILE_NAMES):
                            files_cnt, dirs_cnt, bytes_cnt = _tree_stats(cache_dir_candidate)
                            shutil.rmtree(cache_dir_candidate, ignore_errors=True)
                            report.deleted_files += files_cnt
                            report.deleted_directories += dirs_cnt + 1
                            report.freed_bytes += bytes_cnt
                except OSError as exc:
                    report.errors.append("Browser cache %s: %s" % (cache_dir_candidate, exc))
        report.deleted_items.append("browser_cache")

    # 6. Video Queue
    if "video_queue" in keys_set:
        vq_dir = root / "profile_video_queue"
        if vq_dir.exists() and vq_dir.is_dir():
            _remove_dir_children(vq_dir, report)
            vq_dir.mkdir(parents=True, exist_ok=True)
        report.deleted_items.append("video_queue")

    # 7. Output Videos
    if "output_videos" in keys_set:
        out_root = Path(config.default_output_root).expanduser().resolve()
        if out_root.exists() and out_root.is_dir():
            for child in list(out_root.iterdir()):
                if child.name == "_telegram_inputs":
                    continue
                if child.is_dir():
                    files_cnt, dirs_cnt, bytes_cnt = _tree_stats(child)
                    try:
                        shutil.rmtree(child)
                        report.deleted_files += files_cnt
                        report.deleted_directories += dirs_cnt + 1
                        report.freed_bytes += bytes_cnt
                    except OSError as exc:
                        report.errors.append("%s: %s" % (child, exc))
                elif child.is_file() or child.is_symlink():
                    _delete_single_file(child, report)
        # Stale video database cleanup
        if manager and hasattr(manager, "list_videos") and hasattr(manager, "resolve_video_path") and hasattr(manager, "delete_videos"):
            try:
                missing_video_ids = [
                    video.id
                    for video in manager.list_videos()
                    if not manager.resolve_video_path(video).exists()
                ]
                if missing_video_ids:
                    res = manager.delete_videos(missing_video_ids)
                    report.stale_videos_removed = int((res or {}).get("deleted") or 0)
            except Exception as exc:
                report.errors.append("Stale video DB cleanup: %s" % exc)
        report.deleted_items.append("output_videos")

    # 8. Final output videos only.  Do not remove empty folders or clean stale
    # database records here: those records retain the source URL and image that
    # the user needs to render this video again.
    if "output_video_files_only" in keys_set:
        for video_path in _iter_final_output_video_paths(config, root, manager):
            _delete_single_file(video_path, report)
        report.deleted_items.append("output_video_files_only")

    # 9. Build Dir
    if "build_dir" in keys_set:
        build_dir = root / "build"
        if build_dir.exists() and build_dir.is_dir():
            files_cnt, dirs_cnt, bytes_cnt = _tree_stats(build_dir)
            try:
                shutil.rmtree(build_dir)
                report.deleted_files += files_cnt
                report.deleted_directories += dirs_cnt + 1
                report.freed_bytes += bytes_cnt
            except OSError as exc:
                report.errors.append("Build dir: %s" % exc)
        report.deleted_items.append("build_dir")

    # 10. Legacy Backups
    if "legacy_backups" in keys_set:
        for bk in root.glob("data_backup_before_rebuild_*"):
            if bk.is_dir():
                files_cnt, dirs_cnt, bytes_cnt = _tree_stats(bk)
                try:
                    shutil.rmtree(bk)
                    report.deleted_files += files_cnt
                    report.deleted_directories += dirs_cnt + 1
                    report.freed_bytes += bytes_cnt
                except OSError as exc:
                    report.errors.append("Backup %s: %s" % (bk.name, exc))
        report.deleted_items.append("legacy_backups")

    return report


def format_granular_cleanup_report(report: GranularCleanupReport) -> str:
    """Return user-friendly message describing granular cleanup results."""
    if report.deleted_files == 0 and report.deleted_directories == 0 and report.logs_cleared == 0 and not report.errors:
        return "Không có tệp hoặc dữ liệu nào cần dọn dẹp."
    parts = []
    if report.deleted_files > 0:
        parts.append(f"Đã xóa {report.deleted_files} tệp")
    if report.deleted_directories > 0:
        parts.append(f"{report.deleted_directories} thư mục")
    if report.freed_bytes > 0:
        parts.append(f"giải phóng {_human_size(report.freed_bytes)}")
    if report.logs_cleared > 0:
        parts.append(f"xóa {report.logs_cleared} dòng log")
    if report.stale_videos_removed > 0:
        parts.append(f"dọn {report.stale_videos_removed} bản ghi video mồ côi")

    message = ", ".join(parts) + "."
    if report.errors:
        message += f" (Có {len(report.errors)} mục không xóa được do đang được mở/sử dụng)."
    return message


def format_cleanup_report(report: MediaCleanupReport, *, include_errors: bool = True) -> str:
    if report.deleted_files == 0 and not report.errors:
        return "Không tìm thấy video hoặc ảnh nào trong input/output để xóa."
    parts = ["Đã xóa %s file media" % report.deleted_files]
    if report.deleted_directories:
        parts.append("dọn %s thư mục rỗng" % report.deleted_directories)
    if report.freed_bytes > 0:
        parts.append("giải phóng %s" % _human_size(report.freed_bytes))
    message = ", ".join(parts) + "."
    if include_errors and report.errors:
        message += " Có %s file không xóa được." % len(report.errors)
    return message


def format_tool_cleanup_report(report: MediaCleanupReport, *, include_errors: bool = True) -> str:
    if report.deleted_files == 0 and report.deleted_directories == 0 and not report.errors:
        return "Không tìm thấy dữ liệu tool nào để dọn dẹp."
    parts = ["Đã xóa %s file" % report.deleted_files]
    if report.deleted_directories:
        parts.append("xóa %s thư mục" % report.deleted_directories)
    if report.freed_bytes > 0:
        parts.append("giải phóng %s" % _human_size(report.freed_bytes))
    message = ", ".join(parts) + "."
    if include_errors and report.errors:
        message += " Có %s mục không xóa được." % len(report.errors)
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


def _remove_dir_children(dir_path: Path, report: GranularCleanupReport) -> None:
    try:
        children = list(dir_path.iterdir())
    except OSError as exc:
        report.errors.append("%s: %s" % (dir_path, exc))
        return
    for child in children:
        if child.is_file() or child.is_symlink():
            _delete_single_file(child, report)
        elif child.is_dir():
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


def _delete_single_file(path: Path, report: GranularCleanupReport) -> None:
    try:
        report.freed_bytes += path.stat().st_size
    except OSError:
        pass
    try:
        path.unlink()
        report.deleted_files += 1
    except OSError as exc:
        report.errors.append("%s: %s" % (path, exc))


def _tree_stats(root: Path) -> Tuple[int, int, int]:
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
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(0, int(size_bytes)))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "%d %s" % (int(size), unit)
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % int(size_bytes)
