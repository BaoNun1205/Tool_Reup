"""Shared data types for the TikTok profile manager."""

from __future__ import annotations

from dataclasses import dataclass


LOGIN_TYPES = ("google", "facebook", "email", "phone")
ACCOUNT_STATUSES = ("live", "need_login", "checkpoint", "no_shop", "error", "paused")
VIDEO_STATUSES = (
    "ready",
    "queued",
    "file_selected",
    "prepared",
    "posted",
    "scheduled",
    "product_error",
    "selector_error",
    "error",
    "paused",
)
PUBLISH_MODES = ("now", "scheduled")


@dataclass(frozen=True)
class TikTokAccount:
    id: int
    name: str
    login_type: str
    profile_path: str
    status: str
    note: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TikTokSourceChannel:
    id: int
    account_id: int
    name: str
    url: str
    note: str
    featured: bool
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TikTokVideo:
    id: int
    account_id: int | None
    file_path: str
    caption: str
    hashtags: str
    product_id: str
    publish_mode: str
    scheduled_at: str
    source: str
    status: str
    note: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TikTokLog:
    id: int
    account_id: int | None
    video_id: int | None
    level: str
    action: str
    message: str
    created_at: str
