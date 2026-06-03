"""SQLite storage and profile-folder management for TikTok accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import unicodedata

from auto_tiktok_editor.config import PROJECT_ROOT
from auto_tiktok_editor.tiktok_profiles.models import (
    ACCOUNT_STATUSES,
    LOGIN_TYPES,
    PUBLISH_MODES,
    VIDEO_STATUSES,
    TikTokAccount,
    TikTokLog,
    TikTokVideo,
)


DEFAULT_DB_PATH = PROJECT_ROOT / "tiktok_profile_manager.sqlite3"
DEFAULT_PROFILES_ROOT = PROJECT_ROOT / "profiles"
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\u0080-\uffff]+", re.UNICODE)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
    return slug or "account"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
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
            self._migrate_caption_hashtags(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_account_id ON videos(account_id)")
            conn.execute("DROP TABLE IF EXISTS products")
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
                SELECT id, name, login_type, profile_path, status, note, created_at, updated_at
                FROM accounts
                ORDER BY id ASC
                """
            ).fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_account(self, account_id: int) -> TikTokAccount | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, login_type, profile_path, status, note, created_at, updated_at
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
            expected_path = "profiles/%s" % candidate
            for account in accounts:
                profile_path = account.profile_path.replace("\\", "/").rstrip("/")
                if profile_path == expected_path or Path(profile_path).name == candidate:
                    return account
            for account in accounts:
                if slugify(account.name) == candidate:
                    return account
        return None

    def add_account(self, name: str, login_type: str, note: str = "") -> TikTokAccount:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Account name is required.")
        if login_type not in LOGIN_TYPES:
            raise ValueError("Unsupported login type: %s" % login_type)

        profile_path = self._build_unique_profile_path(clean_name)
        profile_path.mkdir(parents=True, exist_ok=False)
        stored_profile_path = self._path_for_storage(profile_path)
        now = utc_now_iso()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO accounts (name, login_type, profile_path, status, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (clean_name, login_type, stored_profile_path, "paused", note.strip(), now, now),
            )
            account_id = int(cursor.lastrowid)
        account = self.get_account(account_id)
        if account is None:
            raise RuntimeError("Created account could not be loaded.")
        return account

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
                       scheduled_at, source, status, note, created_at, updated_at
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
                       scheduled_at, source, status, note, created_at, updated_at
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
    ) -> TikTokVideo:
        resolved = Path(file_path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError("Video file does not exist: %s" % resolved)
        if account_id is not None and self.get_account(account_id) is None:
            raise ValueError("Account not found: %s" % account_id)
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        clean_caption, clean_hashtags = split_caption_and_hashtags(caption, hashtags)
        stored_path = self._path_for_storage(resolved)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO videos (
                    account_id, file_path, caption, hashtags, product_id, publish_mode,
                    scheduled_at, source, status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    now,
                    now,
                ),
            )
            video_id = int(cursor.lastrowid)
        video = self.get_video(video_id)
        if video is None:
            raise RuntimeError("Created video could not be loaded.")
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
        if account_id is not None and self.get_account(account_id) is None:
            raise ValueError("Account not found: %s" % account_id)
        publish_mode = _normalize_publish_mode(publish_mode)
        scheduled_at = _normalize_scheduled_at(publish_mode, scheduled_at)
        clean_caption, clean_hashtags = split_caption_and_hashtags(caption, hashtags)
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

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> TikTokAccount:
        return TikTokAccount(
            id=int(row["id"]),
            name=row["name"],
            login_type=row["login_type"],
            profile_path=row["profile_path"],
            status=row["status"],
            note=row["note"],
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
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table_name).fetchall()}
        if column_name not in columns:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table_name, column_name, definition))

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
