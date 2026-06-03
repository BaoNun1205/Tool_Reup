"""Bridge completed Telegram jobs into the TikTok Profile Manager video queue."""

from __future__ import annotations

from pathlib import Path
import shutil
import time

from auto_tiktok_editor.config import PROJECT_ROOT, PipelineConfig
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager, slugify, split_caption_and_hashtags


QUEUE_VIDEO_ROOT = PROJECT_ROOT / "profile_video_queue"


def enqueue_telegram_video(
    config: PipelineConfig,
    final_video_path: Path,
    source_title: str | None,
    source_video_url: str,
    product_id: str = "",
    product_url: str = "",
    publish_mode: str = "now",
    scheduled_at: str = "",
    manager: TikTokProfileManager | None = None,
    queue_root: Path | None = None,
) -> bool:
    profile_slug = _profile_slug_from_config(config)
    if not profile_slug:
        return False

    manager = manager or TikTokProfileManager()
    account = manager.find_account_for_profile_slug(profile_slug)
    if account is None:
        manager.add_log(
            "error",
            "telegram_queue",
            "No profile account found for bot/profile slug %s." % profile_slug,
        )
        return False

    queued_video_path = _copy_to_queue(profile_slug, Path(final_video_path), queue_root=queue_root)
    caption, hashtags = split_caption_and_hashtags(source_title or "")
    note_lines = ["Telegram source: %s" % source_video_url]
    if product_url:
        note_lines.append("Product link: %s" % product_url)
    note = "\n".join(note_lines)
    video = manager.add_video(
        queued_video_path,
        caption=caption,
        hashtags=hashtags,
        note=note,
        account_id=account.id,
        product_id=product_id,
        publish_mode=publish_mode,
        scheduled_at=scheduled_at,
        source="telegram",
    )
    manager.add_log(
        "info",
        "telegram_queue",
        "Queued Telegram video %s for profile %s." % (video.file_path, profile_slug),
        account_id=account.id,
        video_id=video.id,
    )
    return True


def _profile_slug_from_config(config: PipelineConfig) -> str:
    configured = str(getattr(config, "tiktok_profile_slug", "") or "").strip()
    if configured:
        return slugify(configured[:-4] if configured.endswith("_bot") else configured)
    return ""


def _copy_to_queue(profile_slug: str, final_video_path: Path, queue_root: Path | None = None) -> Path:
    source = Path(final_video_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError("Final video file does not exist: %s" % source)
    destination_dir = (queue_root or QUEUE_VIDEO_ROOT) / slugify(profile_slug)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / ("%s_%s" % (timestamp, source.name))
    suffix = 2
    while destination.exists():
        destination = destination_dir / ("%s_%02d_%s" % (timestamp, suffix, source.name))
        suffix += 1
    shutil.copy2(str(source), str(destination))
    return destination
