"""SQLite storage and profile-folder management for TikTok accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
from urllib.parse import urlparse

from auto_tiktok_editor.config import PROJECT_ROOT
from auto_tiktok_editor.tiktok_profiles.models import (
    ACCOUNT_STATUSES,
    FASHION_PRODUCT_STATUSES,
    LOGIN_TYPES,
    PUBLISH_MODES,
    VIDEO_CUT_MODES,
    VIDEO_STATUSES,
    TikTokAccount,
    FashionProduct,
    TikTokLog,
    TikTokSourceChannel,
    TikTokVideo,
)


DEFAULT_DB_PATH = PROJECT_ROOT / "tiktok_profile_manager.sqlite3"
DEFAULT_PROFILES_ROOT = PROJECT_ROOT / "profiles"
HASHTAG_RE = re.compile(r"#[\w\u0080-\uffff]+", re.UNICODE)
ACCOUNT_DEFAULT_HASHTAGS = {
    "linh_an_ngon": "#linhanngon",
    "an_vat_cung_tien": "#anvatcungtien",
    "my_me_an_vat": "#mymeanvat",
}


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
    return slug or "account"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_hashtag_for_account_name(account_name: str) -> str:
    normalized = slugify(account_name)
    compact = normalized.replace("_", "")
    if normalized in ACCOUNT_DEFAULT_HASHTAGS:
        return ACCOUNT_DEFAULT_HASHTAGS[normalized]
    for account_slug, hashtag in ACCOUNT_DEFAULT_HASHTAGS.items():
        if compact == account_slug.replace("_", ""):
            return hashtag
    return ""


class TikTokProfileManager:
    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        profiles_root: Path | str = DEFAULT_PROFILES_ROOT,
        project_root: Path | str = PROJECT_ROOT,
    ) -> None:
        self.db_path = Path(db_path)
        self.profiles_root = Path(profiles_root)
        self.project_root = Path(project_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    login_type TEXT NOT NULL,
                    profile_path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    bot_name TEXT NOT NULL DEFAULT '',
                    cut_mode TEXT NOT NULL DEFAULT 'original',
                    hashtags TEXT NOT NULL DEFAULT '',
                    main_image_path TEXT NOT NULL DEFAULT '',
                    auto_use_main_image INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "accounts", "bot_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "cut_mode", "TEXT NOT NULL DEFAULT 'original'")
            account_hashtags_added = self._ensure_column(conn, "accounts", "hashtags", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "main_image_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "auto_use_main_image", "INTEGER NOT NULL DEFAULT 0")
            if account_hashtags_added:
                self._migrate_account_hashtags(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    file_path TEXT NOT NULL UNIQUE,
                    caption TEXT NOT NULL DEFAULT '',
                    hashtags TEXT NOT NULL DEFAULT '',
                    product_id TEXT NOT NULL DEFAULT '',
                    publish_mode TEXT NOT NULL DEFAULT 'now',
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    cut_mode TEXT NOT NULL DEFAULT 'original',
                    source_video_url TEXT NOT NULL DEFAULT '',
                    product_image_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                )
                """
            )
            self._remove_obsolete_video_columns(conn)
            self._ensure_column(conn, "videos", "account_id", "INTEGER")
            self._ensure_column(conn, "videos", "hashtags", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "videos", "product_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "videos", "publish_mode", "TEXT NOT NULL DEFAULT 'now'")
            self._ensure_column(conn, "videos", "scheduled_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "videos", "source", "TEXT NOT NULL DEFAULT 'manual'")
            self._ensure_column(conn, "videos", "cut_mode", "TEXT NOT NULL DEFAULT 'original'")
            self._ensure_column(conn, "videos", "source_video_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "videos", "product_image_path", "TEXT NOT NULL DEFAULT ''")
            self._migrate_caption_hashtags(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_account_id ON videos(account_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fashion_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_url TEXT NOT NULL,
                    product_id TEXT NOT NULL DEFAULT '',
                    product_name TEXT NOT NULL DEFAULT '',
                    image_path TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    caption TEXT NOT NULL DEFAULT '',
                    hashtags TEXT NOT NULL DEFAULT '',
                    video_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'processing',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fashion_products_status ON fashion_products(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fashion_products_created_at ON fashion_products(created_at)")
            conn.execute("DROP TABLE IF EXISTS products")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    featured INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                )
                """
            )
            self._ensure_column(conn, "source_channels", "featured", "INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_channels_account_id ON source_channels(account_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    video_id INTEGER,
                    level TEXT NOT NULL,
                    action TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at)")

    def list_accounts(self) -> list[TikTokAccount]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, login_type, profile_path, status, note, bot_name, cut_mode, hashtags, main_image_path, auto_use_main_image, created_at, updated_at
                FROM accounts
                ORDER BY id ASC
                """
            ).fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_account(self, account_id: int) -> TikTokAccount | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, login_type, profile_path, status, note, bot_name, cut_mode, hashtags, main_image_path, auto_use_main_image, created_at, updated_at
                FROM accounts
                WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
        return self._row_to_account(row) if row else None

    def find_account_for_profile_slug(self, profile_slug: str) -> TikTokAccount | None:
        normalized = slugify(profile_slug)
        candidates = [normalized]
        if normalized.endswith("_bot"):
            candidates.append(normalized[:-4])
        accounts = self.list_accounts()
        for candidate in candidates:
            for account in accounts:
                if slugify(account.bot_name) == candidate:
                    return account
            expected_path = "profiles/%s" % candidate
            for account in accounts:
                profile_path = account.profile_path.replace("\\", "/").rstrip("/")
                if profile_path == expected_path or Path(profile_path).name == candidate:
                    return account
            for account in accounts:
                if slugify(account.name) == candidate:
                    return account
        return None

    def add_account(
        self,
        name: str,
        login_type: str,
        note: str = "",
        bot_name: str = "",
        cut_mode: str = "original",
        hashtags: str | None = None,
        main_image_path: Path | str = "",
        auto_use_main_image: bool = False,
    ) -> TikTokAccount:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Account name is required.")
        if login_type not in LOGIN_TYPES:
            raise ValueError("Unsupported login type: %s" % login_type)
        cut_mode = _normalize_video_cut_mode(cut_mode)
        clean_hashtags = normalize_hashtags(default_hashtag_for_account_name(clean_name) if hashtags is None else hashtags)

        profile_path = self._build_unique_profile_path(clean_name)
        profile_path.mkdir(parents=True, exist_ok=False)
        stored_profile_path = self._path_for_storage(profile_path)
        now = utc_now_iso()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO accounts (name, login_type, profile_path, status, note, bot_name, cut_mode, hashtags, main_image_path, auto_use_main_image, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (clean_name, login_type, stored_profile_path, "paused", note.strip(), _normalize_bot_name(bot_name), cut_mode, clean_hashtags, "", 0, now, now),
            )
            account_id = int(cursor.lastrowid)
        if str(main_image_path or "").strip():
            self.update_account_main_image(account_id, main_image_path, auto_use_main_image)
        elif auto_use_main_image:
            raise ValueError("Cần chọn Main Image trước khi bật Auto dùng Main Image.")
        account = self.get_account(account_id)
        if account is None:
            raise RuntimeError("Created account could not be loaded.")
        return account

    def create_account(
        self,
        name: str,
        login_type: str,
        note: str = "",
        bot_name: str = "",
        cut_mode: str = "original",
        hashtags: str | None = None,
        main_image_path: Path | str = "",
        auto_use_main_image: bool = False,
        profile_path: str = "",
    ) -> TikTokAccount:
        """Compatibility entry point used by the Qt account editor."""
        return self.add_account(
            name=name,
            login_type=login_type,
            note=note,
            bot_name=bot_name,
            cut_mode=cut_mode,
            hashtags=hashtags,
            main_image_path=main_image_path,
            auto_use_main_image=auto_use_main_image,
        )

    def update_account(
        self,
        account_id: int,
        name: str,
        login_type: str,
        note: str = "",
        bot_name: str = "",
        cut_mode: str = "original",
        hashtags: str = "",
        profile_path: str = "",
        main_image_path: Path | str | None = None,
        auto_use_main_image: bool = False,
        clear_main_image: bool = False,
    ) -> TikTokAccount:
        """Save the editable account settings without changing its profile folder."""
        if login_type not in LOGIN_TYPES:
            raise ValueError("Unsupported login type: %s" % login_type)
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Account name is required.")
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE accounts
                SET login_type = ?, note = ?, bot_name = ?, cut_mode = ?, hashtags = ?, auto_use_main_image = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    login_type,
                    note.strip(),
                    _normalize_bot_name(bot_name),
                    _normalize_video_cut_mode(cut_mode),
                    normalize_hashtags(hashtags),
                    1 if auto_use_main_image else 0,
                    now,
                    int(account_id),
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("Account not found: %s" % account_id)
        if clear_main_image:
            account = self.clear_account_main_image(account_id)
        elif main_image_path is not None and str(main_image_path).strip():
            account = self.update_account_main_image(account_id, main_image_path, auto_use_main_image)
        else:
            account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        return account

    def update_account_main_image(
        self,
        account_id: int,
        image_path: Path | str,
        auto_use_main_image: bool | None = None,
    ) -> TikTokAccount:
        """Copy a profile's persistent main image into its profile folder and save it."""
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        source = Path(image_path).expanduser()
        if not source.is_absolute():
            source = self.project_root / source
        source = source.resolve()
        if not source.exists() or not source.is_file():
            raise ValueError("Main Image file does not exist: %s" % source)
        if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise ValueError("Main Image phải là file ảnh (JPG, PNG, WEBP hoặc BMP).")
        profile_dir = self.resolve_profile_path(account)
        profile_dir.mkdir(parents=True, exist_ok=True)
        destination = profile_dir / ("main_image" + (source.suffix.lower() or ".jpg"))
        if source != destination.resolve():
            shutil.copy2(str(source), str(destination))
        now = utc_now_iso()
        stored_path = self._path_for_storage(destination)
        auto_value = bool(account.auto_use_main_image if auto_use_main_image is None else auto_use_main_image)
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET main_image_path = ?, auto_use_main_image = ?, updated_at = ? WHERE id = ?",
                (stored_path, 1 if auto_value else 0, now, int(account_id)),
            )
        updated = self.get_account(account_id)
        if updated is None:
            raise ValueError("Account not found: %s" % account_id)
        return updated

    def clear_account_main_image(self, account_id: int) -> TikTokAccount:
        """Remove the saved Main Image for one profile and disable its Auto setting."""
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        image_path = self.resolve_account_main_image_path(account)
        profile_dir = self.resolve_profile_path(account).resolve()
        if image_path is not None and profile_dir in image_path.resolve().parents:
            image_path.unlink()
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET main_image_path = '', auto_use_main_image = 0, updated_at = ? WHERE id = ?",
                (now, int(account_id)),
            )
        updated = self.get_account(account_id)
        if updated is None:
            raise ValueError("Account not found: %s" % account_id)
        return updated

    def update_account_bot_name(self, account_id: int, bot_name: str) -> TikTokAccount:
        clean_bot_name = _normalize_bot_name(bot_name)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE accounts SET bot_name = ?, updated_at = ? WHERE id = ?",
                (clean_bot_name, now, int(account_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError("Account not found: %s" % account_id)
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        return account

    def update_account_hashtags(self, account_id: int, hashtags: str) -> TikTokAccount:
        clean_hashtags = normalize_hashtags(hashtags)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE accounts SET hashtags = ?, updated_at = ? WHERE id = ?",
                (clean_hashtags, now, int(account_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError("Account not found: %s" % account_id)
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        return account

    def update_account_cut_mode(self, account_id: int, cut_mode: str) -> TikTokAccount:
        normalized = _normalize_video_cut_mode(cut_mode)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE accounts SET cut_mode = ?, updated_at = ? WHERE id = ?",
                (normalized, now, int(account_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError("Account not found: %s" % account_id)
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        return account

    def list_source_channels(self, account_id: int | None = None) -> list[TikTokSourceChannel]:
        with self._connect() as conn:
            if account_id is None:
                rows = conn.execute(
                    """
                    SELECT id, account_id, name, url, note, featured, enabled, created_at, updated_at
                    FROM source_channels
                    ORDER BY account_id ASC, featured DESC, id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, account_id, name, url, note, featured, enabled, created_at, updated_at
                    FROM source_channels
                    WHERE account_id = ?
                    ORDER BY featured DESC, id ASC
                    """,
                    (int(account_id),),
                ).fetchall()
        return [self._row_to_source_channel(row) for row in rows]

    def get_source_channel(self, channel_id: int) -> TikTokSourceChannel | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, account_id, name, url, note, featured, enabled, created_at, updated_at
                FROM source_channels
                WHERE id = ?
                """,
                (int(channel_id),),
            ).fetchone()
        return self._row_to_source_channel(row) if row else None

    def add_source_channel(
        self,
        account_id: int,
        name: str,
        url: str,
        note: str = "",
        featured: bool = False,
        enabled: bool = True,
    ) -> TikTokSourceChannel:
        if self.get_account(int(account_id)) is None:
            raise ValueError("Account not found: %s" % account_id)
        clean_name = (name or "").strip()
        clean_url = _normalize_source_channel_url(url)
        if not clean_name:
            clean_name = _source_channel_name_from_url(clean_url)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_channels (account_id, name, url, note, featured, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(account_id), clean_name, clean_url, note.strip(), 1 if featured else 0, 1 if enabled else 0, now, now),
            )
            channel_id = int(cursor.lastrowid)
        channel = self.get_source_channel(channel_id)
        if channel is None:
            raise RuntimeError("Created source channel could not be loaded.")
        return channel

    def update_source_channel(
        self,
        channel_id: int,
        account_id: int,
        name: str,
        url: str,
        note: str = "",
        featured: bool = False,
        enabled: bool = True,
    ) -> TikTokSourceChannel:
        if self.get_account(int(account_id)) is None:
            raise ValueError("Account not found: %s" % account_id)
        clean_name = (name or "").strip()
        clean_url = _normalize_source_channel_url(url)
        if not clean_name:
            clean_name = _source_channel_name_from_url(clean_url)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE source_channels
                SET account_id = ?, name = ?, url = ?, note = ?, featured = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(account_id), clean_name, clean_url, note.strip(), 1 if featured else 0, 1 if enabled else 0, now, int(channel_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError("Source channel not found: %s" % channel_id)
        channel = self.get_source_channel(channel_id)
        if channel is None:
            raise ValueError("Source channel not found: %s" % channel_id)
        return channel

    def set_source_channel_featured(self, channel_id: int, featured: bool) -> TikTokSourceChannel:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE source_channels SET featured = ?, updated_at = ? WHERE id = ?",
                (1 if featured else 0, now, int(channel_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError("Source channel not found: %s" % channel_id)
        channel = self.get_source_channel(channel_id)
        if channel is None:
            raise ValueError("Source channel not found: %s" % channel_id)
        return channel

    def delete_source_channel(self, channel_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM source_channels WHERE id = ?", (int(channel_id),))
        return bool(cursor.rowcount)

    def update_status(self, account_id: int, status: str, note: str | None = None) -> TikTokAccount:
        if status not in ACCOUNT_STATUSES:
            raise ValueError("Unsupported account status: %s" % status)
        now = utc_now_iso()
        with self._connect() as conn:
            if note is None:
                conn.execute(
                    "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, account_id),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                    (status, note.strip(), now, account_id),
                )
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Account not found: %s" % account_id)
        return account

    def list_videos(self) -> list[TikTokVideo]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, account_id, file_path, caption, hashtags, product_id, publish_mode,
                       scheduled_at, source, status, note, cut_mode, source_video_url,
                       product_image_path, created_at, updated_at
                FROM videos
                ORDER BY id ASC
                """
            ).fetchall()
        return [self._row_to_video(row) for row in rows]

    def get_video(self, video_id: int) -> TikTokVideo | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, account_id, file_path, caption, hashtags, product_id, publish_mode,
                       scheduled_at, source, status, note, cut_mode, source_video_url,
                       product_image_path, created_at, updated_at
                FROM videos
                WHERE id = ?
                """,
                (video_id,),
            ).fetchone()
        return self._row_to_video(row) if row else None

    def add_video(
        self,
        file_path: Path | str,
        caption: str = "",
        hashtags: str = "",
        note: str = "",
        account_id: int | None = None,
        product_id: str = "",
        publish_mode: str = "now",
        scheduled_at: str = "",
        source: str = "manual",
        cut_mode: str | None = None,
        source_video_url: str = "",
        product_image_path: Path | str = "",
    ) -> TikTokVideo:
        resolved = Path(file_path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError("Video file does not exist: %s" % resolved)
        account = self.get_account(account_id) if account_id is not None else None
        if account_id is not None and account is None:
            raise ValueError("Account not found: %s" % account_id)
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        cut_mode = _normalize_video_cut_mode(cut_mode if cut_mode is not None else (account.cut_mode if account else "original"))
        clean_caption, clean_hashtags = split_caption_and_hashtags(caption, hashtags)
        clean_hashtags = _merge_hashtags(clean_hashtags, account.hashtags if account else "")
        stored_path = self._path_for_storage(resolved)
        stored_product_image_path = self._optional_path_for_storage(product_image_path)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO videos (
                    account_id, file_path, caption, hashtags, product_id, publish_mode,
                    scheduled_at, source, status, note, cut_mode, source_video_url,
                    product_image_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    stored_path,
                    clean_caption,
                    clean_hashtags,
                    product_id.strip(),
                    publish_mode,
                    scheduled_at,
                    source.strip() or "manual",
                    "ready",
                    note.strip(),
                    cut_mode,
                    source_video_url.strip(),
                    stored_product_image_path,
                    now,
                    now,
                ),
            )
            video_id = int(cursor.lastrowid)
        video = self.get_video(video_id)
        if video is None:
            raise RuntimeError("Created video could not be loaded.")
        return video

    def add_video_draft(
        self,
        marker_path: Path | str,
        source_video_url: str,
        product_image_path: Path | str,
        caption: str = "",
        hashtags: str = "",
        note: str = "",
        account_id: int | None = None,
        product_id: str = "",
        publish_mode: str = "now",
        scheduled_at: str = "",
        source: str = "telegram",
        cut_mode: str | None = None,
    ) -> TikTokVideo:
        resolved_marker = Path(marker_path).expanduser().resolve()
        resolved_marker.parent.mkdir(parents=True, exist_ok=True)
        if not resolved_marker.exists():
            resolved_marker.write_text("draft\n%s\n" % source_video_url.strip(), encoding="utf-8")
        account = self.get_account(account_id) if account_id is not None else None
        if account_id is not None and account is None:
            raise ValueError("Account not found: %s" % account_id)
        image_path = Path(product_image_path).expanduser().resolve()
        if not image_path.exists() or not image_path.is_file():
            raise ValueError("Product image file does not exist: %s" % image_path)
        if not str(source_video_url or "").strip():
            raise ValueError("Source video URL is required.")
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        cut_mode = _normalize_video_cut_mode(cut_mode if cut_mode is not None else (account.cut_mode if account else "original"))
        clean_caption, clean_hashtags = split_caption_and_hashtags(caption, hashtags)
        clean_hashtags = _merge_hashtags(clean_hashtags, account.hashtags if account else "")
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO videos (
                    account_id, file_path, caption, hashtags, product_id, publish_mode,
                    scheduled_at, source, status, note, cut_mode, source_video_url,
                    product_image_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    self._path_for_storage(resolved_marker),
                    clean_caption,
                    clean_hashtags,
                    product_id.strip(),
                    publish_mode,
                    scheduled_at,
                    source.strip() or "telegram",
                    "draft",
                    note.strip(),
                    cut_mode,
                    source_video_url.strip(),
                    self._path_for_storage(image_path),
                    now,
                    now,
                ),
            )
            video_id = int(cursor.lastrowid)
        video = self.get_video(video_id)
        if video is None:
            raise RuntimeError("Created video draft could not be loaded.")
        return video

    def update_video_schedule(
        self,
        video_id: int,
        publish_mode: str,
        scheduled_at: str = "",
        product_id: str | None = None,
    ) -> TikTokVideo:
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        now = utc_now_iso()
        with self._connect() as conn:
            if product_id is None:
                conn.execute(
                    "UPDATE videos SET publish_mode = ?, scheduled_at = ?, updated_at = ? WHERE id = ?",
                    (publish_mode, scheduled_at, now, video_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE videos
                    SET publish_mode = ?, scheduled_at = ?, product_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (publish_mode, scheduled_at, product_id.strip(), now, video_id),
                )
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        return video

    def update_video_details(
        self,
        video_id: int,
        caption: str,
        hashtags: str = "",
        product_id: str = "",
        publish_mode: str = "now",
        scheduled_at: str = "",
        note: str = "",
        account_id: int | None = None,
    ) -> TikTokVideo:
        current_video = self.get_video(video_id)
        if current_video is None:
            raise ValueError("Video not found: %s" % video_id)
        account = self.get_account(account_id) if account_id is not None else None
        if account_id is not None and account is None:
            raise ValueError("Account not found: %s" % account_id)
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        clean_caption, clean_hashtags = split_caption_and_hashtags(caption, hashtags)
        # Moving a video to a profile must retain its own tags and add the
        # profile defaults, rather than silently discarding either set.
        if account is not None and current_video.account_id != account.id:
            clean_hashtags = _merge_hashtags(clean_hashtags, account.hashtags)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE videos
                SET account_id = ?, caption = ?, hashtags = ?, product_id = ?,
                    publish_mode = ?, scheduled_at = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    account_id,
                    clean_caption,
                    clean_hashtags,
                    product_id.strip(),
                    publish_mode,
                    scheduled_at,
                    note.strip(),
                    now,
                    video_id,
                ),
            )
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        return video

    def update_video_status(self, video_id: int, status: str, note: str | None = None) -> TikTokVideo:
        if status not in VIDEO_STATUSES:
            raise ValueError("Unsupported video status: %s" % status)
        now = utc_now_iso()
        with self._connect() as conn:
            if note is None:
                conn.execute(
                    "UPDATE videos SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, video_id),
                )
            else:
                conn.execute(
                    "UPDATE videos SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                    (status, note.strip(), now, video_id),
                )
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        return video

    def update_video_cut_mode(self, video_id: int, cut_mode: str) -> TikTokVideo:
        normalized = _normalize_video_cut_mode(cut_mode)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE videos SET cut_mode = ?, updated_at = ? WHERE id = ?",
                (normalized, now, video_id),
            )
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        return video

    def mark_video_rendered(
        self,
        video_id: int,
        final_video_path: Path | str,
        source_title: str | None = None,
    ) -> TikTokVideo:
        resolved = Path(final_video_path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError("Final video file does not exist: %s" % resolved)
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        caption = video.caption
        hashtags = video.hashtags
        if source_title and not caption.strip():
            caption, hashtags = split_caption_and_hashtags(source_title, hashtags)
        if video.account_id is not None:
            account = self.get_account(video.account_id)
            if account is not None:
                hashtags = _merge_hashtags(hashtags, account.hashtags)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE videos
                SET file_path = ?, caption = ?, hashtags = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    self._path_for_storage(resolved),
                    caption,
                    hashtags,
                    "ready",
                    now,
                    video_id,
                ),
            )
        video = self.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        return video

    def delete_videos(self, video_ids: list[int]) -> dict:
        report = {
            "deleted": 0,
            "deleted_ids": [],
            "missing_files": 0,
            "errors": [],
        }
        seen = set()
        normalized_ids = []
        for value in video_ids:
            video_id = int(value)
            if video_id in seen:
                continue
            seen.add(video_id)
            normalized_ids.append(video_id)

        for video_id in normalized_ids:
            video = self.get_video(video_id)
            if video is None:
                report["errors"].append("Video not found: %s" % video_id)
                continue

            video_path = self.resolve_video_path(video)
            try:
                if video_path.exists():
                    if not video_path.is_file():
                        report["errors"].append("Video path is not a file: %s" % video_path)
                        continue
                    video_path.unlink()
                else:
                    report["missing_files"] += 1
            except OSError as exc:
                report["errors"].append("Could not delete %s: %s" % (video_path, exc))
                continue

            with self._connect() as conn:
                conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            report["deleted"] += 1
            report["deleted_ids"].append(video_id)

        return report

    def list_fashion_products(self) -> list[FashionProduct]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, product_url, product_id, product_name, image_path, description,
                       caption, hashtags, video_path, status, note, created_at, updated_at
                FROM fashion_products
                ORDER BY id DESC
                """
            ).fetchall()
        return [self._row_to_fashion_product(row) for row in rows]

    def get_fashion_product(self, product_id: int) -> FashionProduct | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, product_url, product_id, product_name, image_path, description,
                       caption, hashtags, video_path, status, note, created_at, updated_at
                FROM fashion_products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
        return self._row_to_fashion_product(row) if row else None

    def add_fashion_product(
        self,
        product_url: str,
        product_id: str,
        product_name: str,
        image_path: Path | str,
        status: str = "processing",
        note: str = "",
    ) -> FashionProduct:
        if status not in FASHION_PRODUCT_STATUSES:
            raise ValueError("Unsupported Fashion product status: %s" % status)
        image = Path(image_path).expanduser().resolve()
        if not image.is_file():
            raise ValueError("Fashion product image does not exist: %s" % image)
        clean_url = str(product_url or "").strip()
        clean_name = str(product_name or "").strip()
        if not clean_url or not clean_name:
            raise ValueError("Fashion product URL and name are required.")
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO fashion_products (
                    product_url, product_id, product_name, image_path, status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_url,
                    str(product_id or "").strip(),
                    clean_name,
                    self._path_for_storage(image),
                    status,
                    str(note or "").strip(),
                    now,
                    now,
                ),
            )
            record_id = int(cursor.lastrowid)
        product = self.get_fashion_product(record_id)
        if product is None:
            raise RuntimeError("Created Fashion product could not be loaded.")
        return product

    def update_fashion_product_copy(
        self,
        product_id: int,
        caption: str,
        hashtags: str,
        description: str | None = None,
        status: str = "ready",
        note: str | None = None,
    ) -> FashionProduct:
        if status not in FASHION_PRODUCT_STATUSES:
            raise ValueError("Unsupported Fashion product status: %s" % status)
        clean_caption = str(caption or "").strip()
        clean_hashtags = normalize_hashtags(hashtags)
        clean_description = str(description or "").strip() or "\n".join(
            part for part in (clean_caption, clean_hashtags) if part
        )
        now = utc_now_iso()
        with self._connect() as conn:
            if note is None:
                conn.execute(
                    """
                    UPDATE fashion_products
                    SET caption = ?, hashtags = ?, description = ?, status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_caption, clean_hashtags, clean_description, status, now, product_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE fashion_products
                    SET caption = ?, hashtags = ?, description = ?, status = ?, note = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_caption, clean_hashtags, clean_description, status, str(note).strip(), now, product_id),
                )
        product = self.get_fashion_product(product_id)
        if product is None:
            raise ValueError("Fashion product not found: %s" % product_id)
        return product

    def update_fashion_product_status(
        self,
        product_id: int,
        status: str,
        note: str | None = None,
    ) -> FashionProduct:
        if status not in FASHION_PRODUCT_STATUSES:
            raise ValueError("Unsupported Fashion product status: %s" % status)
        now = utc_now_iso()
        with self._connect() as conn:
            if note is None:
                conn.execute(
                    "UPDATE fashion_products SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, product_id),
                )
            else:
                conn.execute(
                    "UPDATE fashion_products SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                    (status, str(note).strip(), now, product_id),
                )
        product = self.get_fashion_product(product_id)
        if product is None:
            raise ValueError("Fashion product not found: %s" % product_id)
        return product

    def set_fashion_product_video(self, product_id: int, video_path: Path | str) -> FashionProduct:
        video = Path(video_path).expanduser().resolve()
        if not video.is_file():
            raise ValueError("Fashion video does not exist: %s" % video)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE fashion_products SET video_path = ?, updated_at = ? WHERE id = ?",
                (self._path_for_storage(video), now, product_id),
            )
        product = self.get_fashion_product(product_id)
        if product is None:
            raise ValueError("Fashion product not found: %s" % product_id)
        return product

    def delete_fashion_products(self, product_ids: list[int]) -> dict:
        report = {"deleted": 0, "deleted_ids": [], "missing_files": 0, "errors": []}
        seen = set()
        for raw_id in product_ids:
            product_id = int(raw_id)
            if product_id in seen:
                continue
            seen.add(product_id)
            product = self.get_fashion_product(product_id)
            if product is None:
                report["errors"].append("Fashion product not found: %s" % product_id)
                continue
            for path in (self.resolve_fashion_product_image_path(product), self.resolve_fashion_product_video_path(product)):
                if path is None:
                    continue
                try:
                    try:
                        path.resolve().relative_to(self.project_root.resolve())
                    except ValueError:
                        # A manually attached source outside the project belongs to the user;
                        # remove only its Fashion record, never their original file.
                        continue
                    if path.exists() and path.is_file():
                        path.unlink()
                    elif not path.exists():
                        report["missing_files"] += 1
                except OSError as exc:
                    report["errors"].append("Could not delete %s: %s" % (path, exc))
                    break
            else:
                with self._connect() as conn:
                    conn.execute("DELETE FROM fashion_products WHERE id = ?", (product_id,))
                report["deleted"] += 1
                report["deleted_ids"].append(product_id)
        return report

    def list_logs(self, limit: int = 200) -> list[TikTokLog]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, account_id, video_id, level, action, message, created_at
                FROM logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_log(row) for row in rows]

    def add_log(
        self,
        level: str,
        action: str,
        message: str,
        account_id: int | None = None,
        video_id: int | None = None,
    ) -> TikTokLog:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO logs (account_id, video_id, level, action, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (account_id, video_id, level.strip() or "info", action.strip(), message.strip(), now),
            )
            log_id = int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT id, account_id, video_id, level, action, message, created_at
                FROM logs
                WHERE id = ?
                """,
                (log_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Created log could not be loaded.")
        return self._row_to_log(row)

    def clear_logs(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM logs")
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def resolve_profile_path(self, account: TikTokAccount) -> Path:
        profile_path = Path(account.profile_path)
        if profile_path.is_absolute():
            return profile_path
        return (self.project_root / profile_path).resolve()

    def resolve_video_path(self, video: TikTokVideo) -> Path:
        file_path = Path(video.file_path)
        if file_path.is_absolute():
            return file_path
        return (self.project_root / file_path).resolve()

    def resolve_fashion_product_image_path(self, product: FashionProduct) -> Path | None:
        return self._resolve_optional_stored_path(product.image_path)

    def resolve_fashion_product_video_path(self, product: FashionProduct) -> Path | None:
        return self._resolve_optional_stored_path(product.video_path)

    def resolve_account_main_image_path(self, account: TikTokAccount) -> Path | None:
        raw_path = str(account.main_image_path or "").strip()
        if not raw_path:
            return None
        image_path = Path(raw_path)
        if not image_path.is_absolute():
            image_path = (self.project_root / image_path).resolve()
        return image_path if image_path.exists() and image_path.is_file() else None

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _build_unique_profile_path(self, name: str) -> Path:
        base_slug = slugify(name)
        used_paths = {account.profile_path for account in self.list_accounts()}
        suffix = 1
        while True:
            slug = base_slug if suffix == 1 else "%s_%d" % (base_slug, suffix)
            candidate = self.profiles_root / slug
            stored = self._path_for_storage(candidate)
            if stored not in used_paths and not candidate.exists():
                return candidate
            suffix += 1

    def _path_for_storage(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    def _optional_path_for_storage(self, path: Path | str) -> str:
        if not str(path or "").strip():
            return ""
        return self._path_for_storage(Path(path).expanduser().resolve())

    def _resolve_optional_stored_path(self, value: str) -> Path | None:
        raw_path = str(value or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path)
        return path if path.is_absolute() else (self.project_root / path).resolve()

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> TikTokAccount:
        return TikTokAccount(
            id=int(row["id"]),
            name=row["name"],
            login_type=row["login_type"],
            profile_path=row["profile_path"],
            status=row["status"],
            note=row["note"],
            bot_name=row["bot_name"],
            cut_mode=row["cut_mode"],
            hashtags=row["hashtags"],
            main_image_path=row["main_image_path"],
            auto_use_main_image=bool(row["auto_use_main_image"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_video(row: sqlite3.Row) -> TikTokVideo:
        return TikTokVideo(
            id=int(row["id"]),
            account_id=int(row["account_id"]) if row["account_id"] is not None else None,
            file_path=row["file_path"],
            caption=row["caption"],
            hashtags=row["hashtags"],
            product_id=row["product_id"],
            publish_mode=row["publish_mode"],
            scheduled_at=row["scheduled_at"],
            source=row["source"],
            status=row["status"],
            note=row["note"],
            cut_mode=row["cut_mode"],
            source_video_url=row["source_video_url"],
            product_image_path=row["product_image_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_fashion_product(row: sqlite3.Row) -> FashionProduct:
        return FashionProduct(
            id=int(row["id"]),
            product_url=row["product_url"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            image_path=row["image_path"],
            description=row["description"],
            caption=row["caption"],
            hashtags=row["hashtags"],
            video_path=row["video_path"],
            status=row["status"],
            note=row["note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_source_channel(row: sqlite3.Row) -> TikTokSourceChannel:
        return TikTokSourceChannel(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            name=row["name"],
            url=row["url"],
            note=row["note"],
            featured=bool(row["featured"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> TikTokLog:
        return TikTokLog(
            id=int(row["id"]),
            account_id=int(row["account_id"]) if row["account_id"] is not None else None,
            video_id=int(row["video_id"]) if row["video_id"] is not None else None,
            level=row["level"],
            action=row["action"],
            message=row["message"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> bool:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table_name).fetchall()}
        if column_name not in columns:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table_name, column_name, definition))
            return True
        return False

    @staticmethod
    def _migrate_account_hashtags(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, name, hashtags
            FROM accounts
            WHERE hashtags = ''
            """
        ).fetchall()
        now = utc_now_iso()
        for row in rows:
            default_hashtags = normalize_hashtags(default_hashtag_for_account_name(row["name"]))
            if not default_hashtags:
                continue
            conn.execute(
                "UPDATE accounts SET hashtags = ?, updated_at = ? WHERE id = ?",
                (default_hashtags, now, row["id"]),
            )

    @staticmethod
    def _remove_obsolete_video_columns(conn: sqlite3.Connection) -> None:
        obsolete_columns = {
            "visibility",
            "high_quality_upload",
            "allow_comments",
            "allow_reuse",
            "content_disclosure",
            "ai_generated",
            "copyright_check",
            "quick_content_check",
        }
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
        if not obsolete_columns.intersection(existing_columns):
            return

        conn.execute("ALTER TABLE videos RENAME TO videos_legacy_options")
        conn.execute(
            """
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                file_path TEXT NOT NULL UNIQUE,
                caption TEXT NOT NULL DEFAULT '',
                hashtags TEXT NOT NULL DEFAULT '',
                product_id TEXT NOT NULL DEFAULT '',
                publish_mode TEXT NOT NULL DEFAULT 'now',
                scheduled_at TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                cut_mode TEXT NOT NULL DEFAULT 'original',
                source_video_url TEXT NOT NULL DEFAULT '',
                product_image_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(id)
            )
            """
        )
        kept_columns = (
            "id",
            "account_id",
            "file_path",
            "caption",
            "hashtags",
            "product_id",
            "publish_mode",
            "scheduled_at",
            "source",
            "status",
            "note",
            "cut_mode",
            "source_video_url",
            "product_image_path",
            "created_at",
            "updated_at",
        )
        available_columns = tuple(column for column in kept_columns if column in existing_columns)
        column_sql = ", ".join(available_columns)
        conn.execute(
            "INSERT INTO videos (%s) SELECT %s FROM videos_legacy_options" % (column_sql, column_sql)
        )
        conn.execute("DROP TABLE videos_legacy_options")

    @staticmethod
    def _migrate_caption_hashtags(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, caption, hashtags
            FROM videos
            WHERE caption LIKE '%#%'
            """
        ).fetchall()
        for row in rows:
            caption, hashtags = split_caption_and_hashtags(row["caption"], row["hashtags"])
            if caption == row["caption"] and hashtags == row["hashtags"]:
                continue
            conn.execute(
                "UPDATE videos SET caption = ?, hashtags = ?, updated_at = ? WHERE id = ?",
                (caption, hashtags, utc_now_iso(), row["id"]),
            )


def _normalize_publish_mode(value: str) -> str:
    publish_mode = (value or "now").strip().lower()
    if publish_mode not in PUBLISH_MODES:
        raise ValueError("Unsupported publish mode: %s" % value)
    return publish_mode


def _normalize_video_cut_mode(value: str) -> str:
    normalized = (value or "original").strip().lower()
    if normalized not in VIDEO_CUT_MODES:
        raise ValueError("Unsupported video cut mode: %s" % value)
    return normalized


def _normalize_bot_name(value: str) -> str:
    return str(value or "").strip()


def _normalize_source_channel_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Source channel URL is required.")
    if text.startswith("@"):
        text = "https://www.tiktok.com/%s" % text
    elif not text.startswith(("http://", "https://")):
        if text.startswith("tiktok.com/") or text.startswith("www.tiktok.com/"):
            text = "https://%s" % text
        else:
            text = "https://www.tiktok.com/@%s" % text.lstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Source channel URL is not valid.")
    return text


def _source_channel_name_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if path:
        return path.split("/")[0] or "Source channel"
    return "Source channel"


def normalize_hashtags(value: str) -> str:
    tags = []
    seen = set()
    for raw_part in (value or "").replace(",", " ").split():
        part = raw_part.strip()
        if not part:
            continue
        tag = part if part.startswith("#") else "#%s" % part
        if tag == "#":
            continue
        normalized_key = tag.lower()
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        tags.append(tag)
    return " ".join(tags)


def _merge_hashtags(*values: str) -> str:
    return normalize_hashtags(" ".join(str(value or "") for value in values))


def split_caption_and_hashtags(caption: str, hashtags: str = "") -> tuple[str, str]:
    raw_caption = caption or ""
    found_hashtags = HASHTAG_RE.findall(raw_caption)
    clean_caption = HASHTAG_RE.sub("", raw_caption)
    clean_lines = []
    for line in clean_caption.splitlines():
        clean_line = re.sub(r"[ \t]{2,}", " ", line).strip()
        clean_line = re.sub(r"\s+([,.!?;:])", r"\1", clean_line)
        if clean_line:
            clean_lines.append(clean_line)
    merged_hashtags = " ".join([hashtags or "", *found_hashtags])
    return "\n".join(clean_lines), normalize_hashtags(merged_hashtags)


def _normalize_scheduled_at(publish_mode: str, scheduled_at: str) -> str:
    if publish_mode == "now":
        return ""
    value = (scheduled_at or "").strip()
    if not value:
        raise ValueError("Scheduled time is required.")
    try:
        scheduled_time = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Scheduled time must use YYYY-MM-DD HH:MM format.") from exc
    return scheduled_time.replace(second=0, microsecond=0).isoformat(sep=" ")
