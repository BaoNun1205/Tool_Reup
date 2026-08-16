"""CustomTkinter UI for the TikTok Profile Manager."""

from __future__ import annotations

import calendar
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.app.telegram_bot import TelegramBotClient
from auto_tiktok_editor.app.media_cleanup import cleanup_tool_storage, format_tool_cleanup_report
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec
from auto_tiktok_editor.phone_control import (
    CLOSE_HOTKEY_LABEL,
    DEFAULT_ADB_PORT,
    PhoneController,
    PhoneControlSettings,
    SCREENSHOT_HOTKEY_LABEL,
    WindowsGlobalHotkey,
    load_phone_control_settings,
    normalize_phone_address,
    save_phone_control_settings,
)
from auto_tiktok_editor.telegram_settings import (
    TelegramRuntimeSettings,
    load_telegram_runtime_settings,
    save_telegram_runtime_settings,
)
from auto_tiktok_editor.tiktok_profiles.models import ACCOUNT_STATUSES, LOGIN_TYPES, PUBLISH_MODES
from auto_tiktok_editor.tiktok_profiles.profile_browser import TikTokProfileBrowser
from auto_tiktok_editor.tiktok_profiles.profile_manager import (
    TikTokProfileManager,
    default_hashtag_for_account_name,
    normalize_hashtags,
    slugify,
)
from auto_tiktok_editor.tiktok_profiles.telegram_queue import copy_rendered_video_to_queue


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


COLORS = {
    "bg": "#0B1020",
    "sidebar": "#0F172A",
    "surface": "#151D2F",
    "surface_2": "#1E293B",
    "surface_3": "#273449",
    "border": "#334155",
    "text": "#E5E7EB",
    "muted": "#94A3B8",
    "accent": "#7C3AED",
    "accent_2": "#06B6D4",
    "accent_hover": "#6D28D9",
    "danger": "#EF4444",
    "danger_hover": "#DC2626",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "input": "#0F172A",
    "selected": "#2563EB",
}

FONT = "Segoe UI"
VIETNAM_TZ = timezone(timedelta(hours=7))
PLAY_ICON = "▶"
PLAY_HOVER_ICON = "▶"
SEND_ICON = "\u27A4"
VIDEO_CUT_MODE_LABELS = {
    "fixed": "Fixed chunks",
    "scene": "Scene changes",
    "original": "Keep Original",
    "remove_background": "Xóa nền",
}
VIDEO_CUT_MODE_VALUES = {label: value for value, label in VIDEO_CUT_MODE_LABELS.items()}
VIDEO_ROW_CUT_MODE_LABELS = {
    "fixed": VIDEO_CUT_MODE_LABELS["fixed"],
    "scene": VIDEO_CUT_MODE_LABELS["scene"],
    "original": VIDEO_CUT_MODE_LABELS["original"],
}
VIDEO_ROW_CUT_MODE_VALUES = {label: value for value, label in VIDEO_ROW_CUT_MODE_LABELS.items()}
PRODUCT_IMAGE_CROP_RATIO_LABELS = {
    "1:1": "1:1",
    "4:3": "4:3",
}
PRODUCT_IMAGE_CROP_RATIO_VALUES = {label: value for value, label in PRODUCT_IMAGE_CROP_RATIO_LABELS.items()}
PRODUCT_IMAGE_MOTION_LABELS = {
    "still": "Still",
    "zoom": "Zoom in/out",
}
PRODUCT_IMAGE_MOTION_VALUES = {label: value for value, label in PRODUCT_IMAGE_MOTION_LABELS.items()}
SOURCE_OPEN_LABEL = "Open"
SOURCE_FEATURED_LABEL = "*"
TIKTOK_ANDROID_PACKAGES = (
    "com.ss.android.ugc.trill",
    "com.ss.android.ugc.aweme",
    "com.zhiliaoapp.musically",
    "com.zhiliaoapp.musically.go",
)


def _button_kwargs(kind: str = "secondary") -> dict:
    if kind == "primary":
        return {"fg_color": COLORS["accent"], "hover_color": COLORS["accent_hover"], "text_color": "#FFFFFF"}
    if kind == "danger":
        return {"fg_color": COLORS["danger"], "hover_color": COLORS["danger_hover"], "text_color": "#FFFFFF"}
    return {"fg_color": COLORS["surface_2"], "hover_color": COLORS["surface_3"], "text_color": COLORS["text"]}


def _extract_product_url_from_note(note: str) -> str:
    for line in (note or "").splitlines():
        text = line.strip()
        if "product link:" not in text.lower():
            continue
        value = text.split(":", 1)[1].strip()
        url = value.split()[0].rstrip(".,)]}") if value else ""
        if url.startswith(("http://", "https://")):
            return url
    return ""

def _telegram_bot_config_for_account(payload: dict, account) -> tuple[str, int]:
    bots = payload.get("bots") if isinstance(payload, dict) else None
    if not isinstance(bots, list):
        raise ValueError("telegram_bots.json must contain a 'bots' list.")
    explicit_bot_name = slugify(getattr(account, "bot_name", "") or "")
    if explicit_bot_name:
        for item in bots:
            if not isinstance(item, dict):
                continue
            bot_name = str(item.get("name") or "").strip()
            if slugify(bot_name) != explicit_bot_name:
                continue
            token = str(item.get("bot_token") or item.get("token") or "").strip()
            chat_id = _telegram_bot_chat_id(item)
            if not token:
                raise ValueError("Bot %s is missing bot_token." % bot_name)
            if chat_id is None:
                raise ValueError("Bot %s is missing chat_id." % bot_name)
            return token, chat_id
        raise ValueError("No Telegram bot config matches bot_name %s." % getattr(account, "bot_name", ""))
    candidates = _account_profile_slugs(account)
    for item in bots:
        if not isinstance(item, dict):
            continue
        bot_name = str(item.get("name") or "").strip()
        if not _telegram_bot_name_matches_account(bot_name, candidates):
            continue
        token = str(item.get("bot_token") or item.get("token") or "").strip()
        chat_id = _telegram_bot_chat_id(item)
        if not token:
            raise ValueError("Bot %s is missing bot_token." % bot_name)
        if chat_id is None:
            raise ValueError("Bot %s is missing chat_id." % bot_name)
        return token, chat_id
    raise ValueError("No Telegram bot config matches profile %s." % getattr(account, "name", ""))


def _account_profile_slugs(account) -> set[str]:
    values = {
        slugify(getattr(account, "bot_name", "") or ""),
        slugify(getattr(account, "name", "") or ""),
        slugify(Path(getattr(account, "profile_path", "") or "").name),
    }
    return {value for value in values if value}


def _telegram_bot_name_matches_account(bot_name: str, account_slugs: set[str]) -> bool:
    bot_slug = slugify(bot_name)
    if bot_slug in account_slugs:
        return True
    return bot_slug.endswith("_bot") and bot_slug[:-4] in account_slugs


def _telegram_bot_chat_id(item: dict) -> int | None:
    value = item.get("chat_id") or item.get("delivery_chat_id")
    if value is None and isinstance(item.get("chat_ids"), list) and item.get("chat_ids"):
        value = item.get("chat_ids")[0]
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("chat_id must be a valid integer.")


def _format_vietnam_datetime(value: str, assume_utc: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if assume_utc:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(VIETNAM_TZ)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _compose_video_caption_with_hashtags(caption: str, hashtags: str) -> str:
    parts = []
    clean_caption = str(caption or "").strip()
    clean_hashtags = normalize_hashtags(hashtags)
    if clean_caption:
        parts.append(clean_caption)
    if clean_hashtags:
        parts.append(clean_hashtags)
    return "\n".join(parts).strip()


def _telegram_product_messages_for_video(video) -> tuple[str, str]:
    caption_message = _compose_video_caption_with_hashtags(video.caption, video.hashtags)
    product_id = str(video.product_id or "").strip()
    video_id = getattr(video, "id", "")
    if not caption_message:
        raise ValueError("Video %s chưa có caption/hashtags để gửi Telegram." % video_id)
    if product_id and not product_id.isdigit():
        raise ValueError("Video %s có Product ID không hợp lệ: %s" % (video_id, product_id))
    return caption_message, product_id


def _hashtag_tokens_for_ui(value: str) -> list[str]:
    return [part for part in normalize_hashtags(value).split() if part.startswith("#")]


def _default_hashtag_for_account_name(account_name: str) -> str:
    return default_hashtag_for_account_name(account_name)


def _account_name_from_label(account_label: str) -> str:
    text = str(account_label or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip()
    return text


class YouTubeTagInput(ctk.CTkFrame):
    """Simple YouTube-style hashtag chips with an inline input."""

    def __init__(self, parent, on_change=None, height: int = 112) -> None:
        super().__init__(
            parent,
            height=height,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            border_width=1,
            corner_radius=12,
        )
        self.tags = []  # type: list[str]
        self.on_change = on_change
        self.input_var = tk.StringVar()
        self._columns = 3
        self._tag_height = 32
        self._tag_corner_radius = 8
        self._tag_xpad = 8
        self._tag_gap = 6
        self._tag_top_pad = 9
        self.grid_propagate(False)
        for column in range(self._columns):
            self.grid_columnconfigure(column, weight=1, uniform="yt_tag")
        self.set_hashtags("")

    def set_hashtags(self, value: str | list[str]) -> None:
        self.tags = _hashtag_tokens_for_ui(_coerce_tag_text(value))
        self._render()

    def get_hashtags(self) -> str:
        return " ".join(self.tags)

    def add_hashtag(self, value: str, notify: bool = True) -> bool:
        tokens = _hashtag_tokens_for_ui(value)
        if not tokens:
            return False
        seen = {tag.lower() for tag in self.tags}
        changed = False
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            self.tags.append(token)
            seen.add(key)
            changed = True
        if changed:
            self._render()
            if notify:
                self._emit_change()
        return changed

    def remove_hashtag(self, value: str) -> None:
        key = str(value or "").strip().lower()
        next_tags = [tag for tag in self.tags if tag.lower() != key]
        if next_tags == self.tags:
            return
        self.tags = next_tags
        self._render()
        self._emit_change()

    def _render(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        for index, tag in enumerate(self.tags):
            row = index // self._columns
            column = index % self._columns
            chip = ctk.CTkFrame(
                self,
                fg_color=COLORS["surface_2"],
                border_color=COLORS["border"],
                border_width=1,
                corner_radius=self._tag_corner_radius,
                height=self._tag_height,
            )
            chip.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=(
                    self._tag_xpad if column == 0 else self._tag_gap,
                    self._tag_xpad if column == self._columns - 1 else self._tag_gap,
                ),
                pady=(self._tag_top_pad if row == 0 else self._tag_gap, 0),
            )
            chip.grid_propagate(False)
            chip.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                chip,
                text=tag,
                text_color=COLORS["text"],
                font=(FONT, 12, "bold"),
                anchor="w",
                height=self._tag_height - 6,
            ).grid(row=0, column=0, sticky="ew", padx=(12, 2), pady=3)
            ctk.CTkButton(
                chip,
                text="x",
                width=24,
                height=24,
                command=lambda value=tag: self.remove_hashtag(value),
                fg_color="transparent",
                hover_color=COLORS["surface_3"],
                text_color=COLORS["muted"],
                corner_radius=8,
            ).grid(row=0, column=1, sticky="e", padx=(0, 4), pady=4)

        input_index = len(self.tags)
        input_row = input_index // self._columns
        input_column = input_index % self._columns
        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.input_var,
            placeholder_text="Add tag",
            height=self._tag_height,
            fg_color="#111827",
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            placeholder_text_color=COLORS["muted"],
            corner_radius=self._tag_corner_radius,
        )
        self.entry.grid(
            row=input_row,
            column=input_column,
            sticky="ew",
            padx=(
                self._tag_xpad if input_column == 0 else self._tag_gap,
                self._tag_xpad if input_column == self._columns - 1 else self._tag_gap,
            ),
            pady=(self._tag_top_pad if input_row == 0 else self._tag_gap, 0),
        )
        self.entry.bind("<Return>", self._commit_entry)
        self.entry.bind("<comma>", self._commit_entry)
        self.entry.bind("<FocusOut>", self._commit_entry)

    def _commit_entry(self, _event=None):
        value = self.input_var.get().strip().rstrip(",")
        self.input_var.set("")
        if value:
            self.add_hashtag(value)
        return "break"

    def _emit_change(self) -> None:
        if self.on_change:
            self.on_change(self.get_hashtags())


def _coerce_tag_text(value: str | list[str] | tuple[str, ...] | None) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item or "") for item in value)
    return str(value or "")


class _NavigationAdapter:
    """Compatibility shim for existing code that calls notebook.select(frame)."""

    def __init__(self, app: "App") -> None:
        self.app = app

    def select(self, tab: ctk.CTkFrame) -> None:
        self.app._show_tab(tab)


class CTkDataTable(ctk.CTkFrame):
    """CTk card wrapper around a dark themed ttk.Treeview."""

    _style_configured = False

    def __init__(
        self,
        parent,
        columns: tuple[str, ...],
        specs: tuple[tuple[str, str, int], ...],
        on_select=None,
        on_cell_click=None,
    ) -> None:
        super().__init__(parent, fg_color=COLORS["input"], corner_radius=10)
        self.columns = tuple(columns)
        self.spec_by_column = {column: (label, width) for column, label, width in specs}
        self.displaycolumns = tuple(columns)
        self.on_select = on_select
        self.on_cell_click = on_cell_click
        self._sort_state: dict[str, bool] = {}
        self._column_anchors = {}

        self._configure_treeview_style()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self,
            columns=self.columns,
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
            height=22,
            takefocus=False,
        )
        self.y_scrollbar = ctk.CTkScrollbar(
            self,
            orientation="vertical",
            command=self.tree.yview,
            fg_color=COLORS["input"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["surface_3"],
            width=14,
        )
        self.x_scrollbar = ctk.CTkScrollbar(
            self,
            orientation="horizontal",
            command=self.tree.xview,
            fg_color=COLORS["input"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["surface_3"],
            height=14,
        )
        self.tree.configure(yscrollcommand=self.y_scrollbar.set, xscrollcommand=self.x_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=(6, 0))
        self.y_scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=(6, 0))
        self.x_scrollbar.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))

        self.tree.tag_configure("live", foreground=COLORS["success"])
        self.tree.tag_configure("error", foreground="#FCA5A5")
        self.tree.tag_configure("warning", foreground=COLORS["warning"])
        self.tree.tag_configure("muted", foreground=COLORS["text"])
        self.tree.tag_configure("play_hover", foreground=COLORS["accent_2"])
        self._configure_columns()

        self.tree.bind("<<TreeviewSelect>>", self._handle_select)
        self.tree.bind("<Button-1>", self._handle_click, add="+")

    def bind(self, *args, **kwargs):
        return self.tree.bind(*args, **kwargs)

    def configure(self, **kwargs):  # noqa: D401 - mirror tkinter API
        if "displaycolumns" in kwargs:
            self.displaycolumns = tuple(kwargs.pop("displaycolumns"))
            self.tree.configure(displaycolumns=self.displaycolumns)
            self._configure_columns()
            self._reset_horizontal_scroll()
            self.after_idle(self._reset_horizontal_scroll)
        if kwargs:
            return super().configure(**kwargs)
        return None

    def _reset_horizontal_scroll(self) -> None:
        try:
            self.update_idletasks()
            self.tree.xview_moveto(0)
        except tk.TclError:
            pass

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())

    def delete(self, *items) -> None:
        if not items:
            self.clear()
            return
        existing = [str(item) for item in items if self.tree.exists(str(item))]
        if existing:
            self.tree.delete(*existing)

    def insert(self, parent, index, iid: str, values: tuple, tags: tuple = ()) -> str:
        row_id = str(iid)
        if self.tree.exists(row_id):
            self.tree.delete(row_id)
        self.tree.insert(parent, index, iid=row_id, values=values, tags=tags)
        return row_id

    def update_row(self, iid: str, values: tuple, tags: tuple = ()) -> None:
        row_id = str(iid)
        if self.tree.exists(row_id):
            self.tree.item(row_id, values=values, tags=tags)
        else:
            self.insert("", tk.END, row_id, values, tags)

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.tree.get_children())

    def exists(self, item: str) -> bool:
        return self.tree.exists(str(item))

    def selection(self) -> tuple[str, ...]:
        return tuple(self.tree.selection())

    def selection_set(self, item: str) -> None:
        row_id = str(item)
        if not self.tree.exists(row_id):
            return
        self.tree.selection_set(row_id)
        self.tree.focus(row_id)
        self.tree.see(row_id)
        if self.on_select:
            self.on_select()

    def item(self, item: str, option: str | None = None, **kwargs):
        return self.tree.item(str(item), option, **kwargs)

    def identify_cell(self, x: int, y: int) -> tuple[str, str]:
        if self.tree.identify_region(x, y) != "cell":
            return "", ""
        row_id = self.tree.identify_row(y)
        column_token = self.tree.identify_column(x)
        if not row_id or not column_token:
            return "", ""
        try:
            display_index = int(column_token.replace("#", "")) - 1
            column = self.displaycolumns[display_index]
        except (ValueError, IndexError):
            return "", ""
        return row_id, column

    def set_column_alignment(self, column: str, anchor) -> None:
        if column not in self.columns:
            return
        self._column_anchors[column] = anchor
        self.tree.heading(column, anchor=anchor)
        self.tree.column(column, anchor=anchor)

    def _configure_columns(self) -> None:
        for column in self.columns:
            label, width = self.spec_by_column[column]
            anchor = self._column_anchors.get(column, tk.W)
            self.tree.heading(
                column,
                text=label,
                anchor=anchor,
                command=lambda col=column: self._sort_by_column(col),
            )
            stretch = column in self.displaycolumns
            self.tree.column(
                column,
                width=width,
                minwidth=max(64, min(width, 180)),
                anchor=anchor,
                stretch=stretch,
            )
        self.tree.configure(displaycolumns=self.displaycolumns)

    def _handle_select(self, _event=None) -> None:
        if self.on_select:
            self.on_select()

    def _handle_click(self, event) -> None:
        row_id, column = self.identify_cell(event.x, event.y)
        if not row_id or not column:
            return
        if self.on_cell_click:
            self.on_cell_click(row_id, column)

    def _sort_by_column(self, column: str) -> None:
        rows = []
        column_index = self.columns.index(column)
        for row_id in self.tree.get_children():
            values = self.tree.item(row_id, "values")
            value = values[column_index] if column_index < len(values) else ""
            rows.append((self._sort_value(value), row_id))
        reverse = not self._sort_state.get(column, False)
        rows.sort(reverse=reverse)
        for index, (_value, row_id) in enumerate(rows):
            self.tree.move(row_id, "", index)
        self._sort_state[column] = reverse

    def _sort_value(self, value):
        text = str(value or "")
        try:
            return (0, int(text))
        except ValueError:
            return (1, text.lower())

    @classmethod
    def _configure_treeview_style(cls) -> None:
        if cls._style_configured:
            return
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Dark.Treeview",
            background=COLORS["input"],
            fieldbackground=COLORS["input"],
            foreground=COLORS["text"],
            rowheight=48,
            borderwidth=0,
            relief="flat",
            font=(FONT, 14),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=COLORS["surface_2"],
            foreground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            font=(FONT, 14, "bold"),
            padding=(6, 12),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", COLORS["selected"])],
            foreground=[("selected", "#FFFFFF")],
        )
        style.map("Dark.Treeview.Heading", background=[("active", COLORS["surface_3"])])
        cls._style_configured = True


class App(ctk.CTk):
    def __init__(
        self,
        manager: TikTokProfileManager | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        super().__init__()
        self.withdraw()
        self.root = self
        self.config = config or PipelineConfig.from_env()
        self.logger = logging.getLogger("auto_tiktok_editor.tiktok_profiles.ui")
        self.manager = manager or TikTokProfileManager()
        self.browser = TikTokProfileBrowser(self.manager)
        self.events: queue.Queue = queue.Queue()
        self.browser_requests: queue.Queue = queue.Queue()
        self.video_snapshot = ()
        self.log_snapshot = ()
        self.source_snapshot = ()
        self.video_detail_dirty = False
        self.video_detail_loading = False
        self.video_detail_autosave_after = None
        self.video_detail_video_id = None
        self.video_detail_restoring_selection = False
        self.video_render_pending_ids = set()
        self.busy = False
        self.closing = False
        self.action_buttons = []
        self.telegram_bot_process: subprocess.Popen | None = None
        self.telegram_active_profile_slug: str | None = None
        self.telegram_bot_pause_path: Path | None = None
        self.telegram_log_seen_ids = set()
        self.phone_controls_ui_state: tuple[bool, bool] | None = None
        self.telegram_controls_ui_state: tuple[bool, bool, str] | None = None
        self.phone_controller = PhoneController(self.config, on_event=self._queue_phone_event)
        self.video_render_executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(self.config, "max_parallel_session_items", 2) or 1)),
            thread_name_prefix="profile-video-render",
        )
        self.phone_screenshot_hotkey = WindowsGlobalHotkey(
            self._queue_phone_screenshot_hotkey,
            virtual_key=0x53,
            thread_name="phone-screenshot-hotkey",
        )
        self.phone_close_hotkey = WindowsGlobalHotkey(
            self._queue_phone_close_hotkey,
            virtual_key=0x51,
            thread_name="phone-close-hotkey",
        )
        phone_settings = load_phone_control_settings()
        self.phone_address_var = tk.StringVar(value=phone_settings.address)
        phone_ip, phone_port = self._split_phone_address(phone_settings.address)
        self.phone_ip_var = tk.StringVar(value=phone_ip)
        self.phone_port_var = tk.StringVar(value=phone_port)
        self.phone_keep_screen_awake_var = tk.BooleanVar(value=phone_settings.keep_screen_awake)
        self.phone_turn_screen_off_var = tk.BooleanVar(value=phone_settings.turn_screen_off)
        self.phone_always_on_top_var = tk.BooleanVar(value=phone_settings.always_on_top)
        self.phone_monitor_target_var = tk.StringVar(
            value="Secondary" if phone_settings.monitor_target == "secondary" else "Main"
        )
        self.phone_dock_position_var = tk.StringVar(
            value={
                "left": "Dock left",
                "right": "Dock right",
            }.get(phone_settings.dock_position, "Off")
        )
        self.phone_max_fps_var = tk.StringVar(value=str(phone_settings.max_fps))
        self.phone_max_size_var = tk.StringVar(value=str(phone_settings.max_size))
        self.phone_video_bit_rate_var = tk.StringVar(
            value=phone_settings.video_bit_rate.replace("M", " Mbps")
        )
        self.phone_control_status_var = tk.StringVar(value="Phone control stopped")
        self.phone_last_transfer_var = tk.StringVar(value="No files transferred in this session.")
        self.phone_metric_var = tk.StringVar(value="Stopped")
        self.telegram_metric_var = tk.StringVar(value="Stopped")
        self.telegram_add_form_visible = False
        self.telegram_add_form_previous_values: tuple[str, str, str] | None = None
        telegram_settings = load_telegram_runtime_settings()
        self.telegram_bot_name_var = tk.StringVar(value="")
        self.telegram_bot_token_var = tk.StringVar(value="")
        self.telegram_chat_id_var = tk.StringVar(value=str(self.config.telegram_delivery_chat_id or telegram_settings.delivery_chat_id or ""))
        self.telegram_send_result_var = tk.BooleanVar(
            value=bool(getattr(self.config, "telegram_send_result_to_telegram", telegram_settings.send_result_to_telegram))
        )
        self.telegram_save_profile_var = tk.BooleanVar(
            value=bool(getattr(self.config, "telegram_save_received_video_to_profile", telegram_settings.save_received_video_to_profile))
        )
        cut_mode = str(getattr(self.config, "video_cut_mode", telegram_settings.video_cut_mode) or "fixed").strip().lower()
        if cut_mode not in VIDEO_CUT_MODE_LABELS:
            cut_mode = "fixed"
        self.video_cut_mode_var = tk.StringVar(value=VIDEO_CUT_MODE_LABELS[cut_mode])
        self.fixed_chunk_duration_var = tk.StringVar(
            value=self._format_float_setting(
                getattr(self.config, "fixed_chunk_duration_seconds", telegram_settings.fixed_chunk_duration_seconds)
            )
        )
        self.scene_threshold_var = tk.StringVar(
            value=self._format_float_setting(getattr(self.config, "scene_threshold", telegram_settings.scene_threshold))
        )
        crop_ratio = str(
            getattr(self.config, "product_image_crop_ratio", telegram_settings.product_image_crop_ratio) or "1:1"
        ).strip().lower().replace("x", ":")
        if crop_ratio not in PRODUCT_IMAGE_CROP_RATIO_LABELS:
            crop_ratio = "1:1"
        self.product_image_crop_ratio_var = tk.StringVar(value=PRODUCT_IMAGE_CROP_RATIO_LABELS[crop_ratio])
        image_motion = str(
            getattr(self.config, "product_image_motion", telegram_settings.product_image_motion) or "still"
        ).strip().lower()
        if image_motion not in PRODUCT_IMAGE_MOTION_LABELS:
            image_motion = "still"
        self.product_image_motion_var = tk.StringVar(value=PRODUCT_IMAGE_MOTION_LABELS[image_motion])
        self.telegram_bot_status_var = tk.StringVar(value="Bot stopped")
        self.telegram_target_profile_var = tk.StringVar(value="Telegram videos will be saved to: Select a profile")
        self.product_link_tooltip = None
        self.product_link_tooltip_url = ""
        self.product_link_tooltip_row_id = ""
        self.product_link_tooltip_after = None
        self.success_notification = None
        self.success_notification_after = None
        self.account_hashtags_editor = None
        self.account_hashtags_editor_info = None
        self.video_play_hover_row_id = ""
        self.app_icon_image = None
        self.source_account_var = tk.StringVar(value="No accounts")
        self.source_detail_account_var = tk.StringVar(value="No accounts")
        self.source_account_ids_by_label: dict[str, int | None] = {"No accounts": None}
        self.source_name_var = tk.StringVar(value="")
        self.source_url_var = tk.StringVar(value="")
        self.source_featured_var = tk.BooleanVar(value=False)
        self.source_editing_id: int | None = None

        self.title("TikTok Profile Manager")
        self._apply_app_icon()
        self.geometry("1320x780")
        self.minsize(1100, 640)
        self.configure(fg_color=COLORS["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.browser_thread = threading.Thread(target=self._browser_worker_loop, daemon=True)
        self.browser_thread.start()

        self.build_sidebar()
        self.build_main_content()
        self.build_status_bar()
        self._refresh_all()
        self.telegram_log_seen_ids = {log.id for log in self.manager.list_logs()}
        self.update_idletasks()
        self.after(10, self._show_ready_window)
        self.after(150, self._poll_events)
        self.after(1500, self._sync_telegram_bot_button)
        self.after(1800, self._sync_phone_control_status)
        self.after(2000, self._poll_database_changes)

    def _asset_path(self, filename: str) -> Path | None:
        candidates = []
        executable_parent = Path(sys.executable).resolve().parent
        candidates.append(executable_parent / "assets" / filename)
        candidates.append(Path.cwd() / "assets" / filename)
        try:
            source_root = Path(__file__).resolve().parents[3]
            candidates.append(source_root / "assets" / filename)
        except IndexError:
            pass
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _apply_app_icon(self) -> None:
        ico_path = self._asset_path("app_icon.ico")
        if ico_path is not None:
            try:
                self.iconbitmap(default=str(ico_path))
            except tk.TclError as exc:
                self.logger.debug("Could not set app iconbitmap: %s", exc)
        png_path = self._asset_path("app_icon.png")
        if png_path is not None:
            try:
                self.app_icon_image = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self.app_icon_image)
            except tk.TclError as exc:
                self.logger.debug("Could not set app iconphoto: %s", exc)

    def _show_ready_window(self) -> None:
        if self.closing:
            return
        self._maximize_window()
        self.deiconify()
        self.lift()

    def _show_success_notification(self, message: str, duration_ms: int = 3000) -> None:
        if self.closing:
            return
        self._hide_success_notification()
        notification = tk.Toplevel(self)
        notification.withdraw()
        notification.overrideredirect(True)
        notification.configure(bg=COLORS["success"])
        try:
            notification.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            notification,
            text=message,
            bg=COLORS["success"],
            fg="#052E16",
            padx=18,
            pady=10,
            justify="left",
            font=(FONT, 11, "bold"),
        )
        label.pack(fill="both", expand=True)
        notification.update_idletasks()
        width = min(max(label.winfo_reqwidth(), 260), 520)
        height = max(label.winfo_reqheight(), 44)
        x = self.winfo_rootx() + max(16, self.winfo_width() - width - 32)
        y = self.winfo_rooty() + 28
        notification.geometry("%dx%d+%d+%d" % (width, height, x, y))
        notification.deiconify()
        self.success_notification = notification
        self.success_notification_after = self.after(duration_ms, self._hide_success_notification)

    def _hide_success_notification(self) -> None:
        if self.success_notification_after is not None:
            try:
                self.after_cancel(self.success_notification_after)
            except tk.TclError:
                pass
            self.success_notification_after = None
        notification = self.success_notification
        self.success_notification = None
        if notification is None:
            return
        try:
            notification.destroy()
        except tk.TclError:
            pass

    def _maximize_window(self) -> None:
        try:
            width = self.winfo_screenwidth()
            height = self.winfo_screenheight()
            self.geometry("%dx%d+0+0" % (width, height))
        except tk.TclError:
            pass
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def build_sidebar(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=230, fg_color=COLORS["sidebar"], corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(self.sidebar, text="TikTok", font=(FONT, 23, "bold"), text_color=COLORS["text"]).pack(anchor="w", padx=20, pady=(24, 0))
        ctk.CTkLabel(self.sidebar, text="Profile Manager", font=(FONT, 13), text_color=COLORS["muted"]).pack(anchor="w", padx=20, pady=(2, 18))

        self.nav_buttons = {}
        nav_items = (("Dashboard", "dashboard"), ("Sources", "sources"), ("Videos", "videos"), ("Activity", "activity"))
        for label, key in nav_items:
            button = ctk.CTkButton(
                self.sidebar,
                text=label,
                anchor="w",
                height=40,
                corner_radius=10,
                command=lambda tab=key: self._show_tab_name(tab),
                **_button_kwargs("secondary"),
            )
            button.pack(fill="x", padx=14, pady=4)
            self.nav_buttons[key] = button

        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=18, pady=18)
        ctk.CTkLabel(footer, text="Workspace", font=(FONT, 11, "bold"), text_color=COLORS["muted"]).pack(anchor="w")
        ctk.CTkLabel(
            footer,
            text=str(self.manager.project_root),
            font=(FONT, 11),
            text_color=COLORS["muted"],
            wraplength=190,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def build_main_content(self) -> None:
        self.main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew", padx=24, pady=(18, 0))
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self.account_count_var = tk.StringVar(value="0")
        self.video_count_var = tk.StringVar(value="0")
        self.log_count_var = tk.StringVar(value="0")
        metrics = ctk.CTkFrame(self.main, fg_color="transparent")
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        for index in range(4):
            metrics.grid_columnconfigure(index, weight=1, uniform="metric")
        for index, (label, variable) in enumerate(
            (
                ("Accounts", self.account_count_var),
                ("Videos", self.video_count_var),
                ("Telegram", self.telegram_metric_var),
                ("Phone", self.phone_metric_var),
            )
        ):
            card = ctk.CTkFrame(metrics, fg_color=COLORS["surface"], corner_radius=14)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0 if index == 3 else 8))
            ctk.CTkLabel(card, textvariable=variable, font=(FONT, 22, "bold"), text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(12, 0))
            ctk.CTkLabel(card, text=label, font=(FONT, 12), text_color=COLORS["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

        self.content_stack = ctk.CTkFrame(self.main, fg_color="transparent")
        self.content_stack.grid(row=1, column=0, sticky="nsew")
        self.content_stack.grid_columnconfigure(0, weight=1)
        self.content_stack.grid_rowconfigure(0, weight=1)

        self.dashboard_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.sources_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.videos_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.activity_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.tab_by_name = {
            "dashboard": self.dashboard_tab,
            "sources": self.sources_tab,
            "videos": self.videos_tab,
            "activity": self.activity_tab,
        }
        for tab in self.tab_by_name.values():
            tab.grid(row=0, column=0, sticky="nsew")
        self.notebook = _NavigationAdapter(self)

        self._build_dashboard_tab()
        self._build_sources_tab()
        self._build_videos_tab()
        self._build_activity_tab()
        self._show_tab_name("dashboard")

    def build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready.")
        self.status_bar = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.status_bar.grid(row=1, column=1, sticky="ew", padx=24, pady=(8, 14))
        self.status_bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.status_bar, textvariable=self.status_var, text_color=COLORS["muted"], font=(FONT, 12), anchor="w").grid(row=0, column=0, sticky="ew")
        self.busy_progress = ctk.CTkProgressBar(self.status_bar, mode="indeterminate", width=170, progress_color=COLORS["accent_2"])
        self.busy_progress.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.busy_progress.grid_remove()

    def _card(self, parent, title: str, subtitle: str = "", compact: bool = False):
        card = ctk.CTkFrame(parent, fg_color=COLORS["surface"], corner_radius=14)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        header.grid_columnconfigure(0, weight=1)
        title_stack = ctk.CTkFrame(header, fg_color="transparent")
        title_stack.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title_stack, text=title, font=(FONT, 16, "bold"), text_color=COLORS["text"], height=20).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(title_stack, text=subtitle, font=(FONT, 12), text_color=COLORS["muted"], height=18).pack(anchor="w", pady=(2, 0))
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        if compact:
            card.configure(height=1)
            header.configure(height=1)
            title_stack.configure(height=1)
            actions.configure(height=1)
            body.configure(height=1)
        return card, body, actions

    def _entry(self, parent, variable=None, placeholder: str = ""):
        return ctk.CTkEntry(
            parent,
            textvariable=variable,
            placeholder_text=placeholder,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            height=34,
        )

    def _textbox(self, parent, height: int):
        return ctk.CTkTextbox(
            parent,
            height=height,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            border_width=1,
            text_color=COLORS["text"],
            corner_radius=8,
            font=(FONT, 12),
        )

    def _option_menu(self, parent, variable, values, command=None):
        return ctk.CTkOptionMenu(
            parent,
            variable=variable,
            values=list(values),
            command=command,
            height=38,
            corner_radius=10,
            fg_color=COLORS["input"],
            button_color=COLORS["surface_2"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["input"],
            dropdown_hover_color=COLORS["accent"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 14),
            dropdown_font=(FONT, 14),
            dynamic_resizing=False,
            anchor="w",
        )

    def _build_dashboard_tab(self) -> None:
        self.dashboard_tab.grid_columnconfigure(0, weight=1)
        self.dashboard_tab.grid_rowconfigure(0, weight=1)
        self.dashboard_content = ctk.CTkFrame(
            self.dashboard_tab,
            fg_color="transparent",
        )
        self.dashboard_content.grid(row=0, column=0, sticky="nsew")
        self.dashboard_content.grid_columnconfigure(0, weight=1)
        self.dashboard_content.grid_columnconfigure(1, weight=1)
        self.dashboard_content.grid_rowconfigure(2, weight=1)
        self._build_accounts_section(self.dashboard_content)
        self._build_phone_control_section(self.dashboard_content, row=1, column=0)
        self._build_telegram_bot_section(self.dashboard_content, row=1, column=1)
        self._build_live_console_section(self.dashboard_content, row=2, column=0)

    def _build_accounts_section(self, parent) -> None:
        card, body, actions = self._card(
            parent,
            "Account / Profile Management",
            "Profiles, login method, folder, and live status.",
        )
        card.configure(height=310)
        card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        card.grid_propagate(False)
        self._add_action_button(actions, "Add account", self._add_account, "primary")
        for text, command, kind in (
            ("Open TikTok Studio", self._open_tiktok_studio, "secondary"),
            ("Mark Live", self._mark_selected_account_live, "secondary"),
            ("Auto Post", self._auto_post_selected_account_videos, "primary"),
            ("Refresh", self._refresh_all, "secondary"),
            ("Cleanup", self._cleanup_tool_storage, "danger"),
        ):
            self._add_action_button(actions, text, command, kind)
        self.account_table = CTkDataTable(
            body,
            ("id", "name", "bot_name", "login_type", "status", "cut_mode", "hashtags", "profile_path", "note", "updated_at"),
            (
                ("id", "ID", 60),
                ("name", "Name", 170),
                ("bot_name", "Bot Name", 170),
                ("login_type", "Login", 90),
                ("status", "Status", 110),
                ("cut_mode", "Cut Mode", 170),
                ("hashtags", "Hashtags", 260),
                ("profile_path", "Profile Path", 260),
                ("note", "Note", 220),
                ("updated_at", "Updated", 170),
            ),
            on_select=self._on_account_selected,
            on_cell_click=self._on_account_cell_clicked,
        )
        self.account_table.configure(displaycolumns=("id", "name", "bot_name", "login_type", "status", "cut_mode", "hashtags", "profile_path", "updated_at"))
        self.account_table.set_column_alignment("cut_mode", tk.CENTER)
        self.account_table.bind("<Double-1>", self._on_account_cell_double_clicked, add="+")
        self.account_table.pack(fill="both", expand=True)

    def _build_video_edit_settings_content(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Video Edit Settings",
            font=(FONT, 14, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w")

        switches = ctk.CTkFrame(header, fg_color="transparent")
        switches.grid(row=0, column=1, sticky="e", padx=(12, 10))
        self.telegram_send_switch = ctk.CTkSwitch(
            switches,
            text="Send result",
            variable=self.telegram_send_result_var,
            command=self._on_telegram_settings_changed,
            width=108,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 11),
        )
        self.telegram_send_switch.pack(side="left", padx=(0, 10))
        self.telegram_save_switch = ctk.CTkSwitch(
            switches,
            text="Save to profile",
            variable=self.telegram_save_profile_var,
            command=self._on_telegram_settings_changed,
            width=126,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 11),
        )
        self.telegram_save_switch.pack(side="left")

        self.video_settings_save_button = ctk.CTkButton(
            header,
            text="Save",
            width=60,
            height=28,
            command=self._on_video_edit_settings_changed,
            **_button_kwargs("primary"),
        )
        self.video_settings_save_button.grid(row=0, column=2, sticky="e")
        self.action_buttons.append(self.video_settings_save_button)

        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="w")

        chunk_frame = ctk.CTkFrame(controls, fg_color="transparent")
        chunk_frame.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ctk.CTkLabel(chunk_frame, text="Chunk (s)", text_color=COLORS["muted"], font=(FONT, 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        self.fixed_chunk_duration_entry = self._entry(chunk_frame, self.fixed_chunk_duration_var, "2.27")
        self.fixed_chunk_duration_entry.configure(width=92, height=30)
        self.fixed_chunk_duration_entry.grid(row=1, column=0, sticky="ew")

        threshold_frame = ctk.CTkFrame(controls, fg_color="transparent")
        threshold_frame.grid(row=0, column=1, sticky="w", padx=(0, 8))
        ctk.CTkLabel(threshold_frame, text="Threshold", text_color=COLORS["muted"], font=(FONT, 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        self.scene_threshold_entry = self._entry(threshold_frame, self.scene_threshold_var, "0.35")
        self.scene_threshold_entry.configure(width=92, height=30)
        self.scene_threshold_entry.grid(row=1, column=0, sticky="ew")

        ratio_frame = ctk.CTkFrame(controls, fg_color="transparent")
        ratio_frame.grid(row=0, column=2, sticky="w", padx=(0, 8))
        ctk.CTkLabel(ratio_frame, text="Crop", text_color=COLORS["muted"], font=(FONT, 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        self.product_image_crop_ratio_menu = self._option_menu(
            ratio_frame,
            self.product_image_crop_ratio_var,
            list(PRODUCT_IMAGE_CROP_RATIO_VALUES.keys()),
            command=lambda _value: self._on_video_edit_settings_changed(),
        )
        self.product_image_crop_ratio_menu.configure(width=88, height=30)
        self.product_image_crop_ratio_menu.grid(row=1, column=0, sticky="ew")

        motion_frame = ctk.CTkFrame(controls, fg_color="transparent")
        motion_frame.grid(row=0, column=3, sticky="w")
        ctk.CTkLabel(motion_frame, text="Motion", text_color=COLORS["muted"], font=(FONT, 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        self.product_image_motion_menu = self._option_menu(
            motion_frame,
            self.product_image_motion_var,
            list(PRODUCT_IMAGE_MOTION_VALUES.keys()),
            command=lambda _value: self._on_video_edit_settings_changed(),
        )
        self.product_image_motion_menu.configure(width=118, height=30)
        self.product_image_motion_menu.grid(row=1, column=0, sticky="ew")
        self._update_video_edit_controls_state()

    def _build_sources_tab(self) -> None:
        self.sources_tab.grid_columnconfigure(0, weight=1)
        self.sources_tab.grid_columnconfigure(1, weight=0, minsize=420)
        self.sources_tab.grid_rowconfigure(0, weight=1)

        table_card, table_body, table_actions = self._card(
            self.sources_tab,
            "Sources",
            "TikTok channels used as video sources for each profile.",
        )
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        account_filter = ctk.CTkFrame(table_actions, fg_color="transparent")
        account_filter.grid(row=0, column=0, sticky="e", padx=(0, 8))
        ctk.CTkLabel(
            account_filter,
            text="Profile",
            text_color=COLORS["muted"],
            font=(FONT, 11, "bold"),
        ).pack(side="left", padx=(0, 6))
        self.source_account_menu = self._option_menu(
            account_filter,
            self.source_account_var,
            ["No accounts"],
            command=lambda _value: self._on_source_account_changed(),
        )
        self.source_account_menu.configure(width=230)
        self.source_account_menu.pack(side="left")
        self._add_action_button(table_actions, "New", self._new_source_channel, "secondary")
        self._add_action_button(table_actions, "Refresh", self._refresh_sources, "secondary")

        self.source_table = CTkDataTable(
            table_body,
            ("row_no", "featured", "open", "name", "url", "note", "updated_at"),
            (
                ("row_no", "#", 60),
                ("featured", "Featured", 90),
                ("open", "Action", 80),
                ("name", "Channel", 190),
                ("url", "URL", 360),
                ("note", "Note", 260),
                ("updated_at", "Updated", 150),
            ),
            on_select=self._on_source_selected,
            on_cell_click=self._on_source_cell_click,
        )
        self.source_table.configure(displaycolumns=("row_no", "featured", "open", "name", "url", "note", "updated_at"))
        self.source_table.set_column_alignment("row_no", tk.CENTER)
        self.source_table.set_column_alignment("featured", tk.CENTER)
        self.source_table.set_column_alignment("open", tk.CENTER)
        self.source_table.pack(fill="both", expand=True)

        detail_card = ctk.CTkFrame(self.sources_tab, width=420, fg_color=COLORS["surface"], corner_radius=14)
        detail_card.grid(row=0, column=1, sticky="nsew")
        detail_card.grid_propagate(False)
        detail_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            detail_card,
            text="Source Detail",
            font=(FONT, 16, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            detail_card,
            text="Save channels for the selected profile and open them on the connected phone.",
            font=(FONT, 12),
            text_color=COLORS["muted"],
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))

        form = ctk.CTkFrame(detail_card, fg_color="transparent")
        form.grid(row=2, column=0, sticky="ew", padx=18)
        form.grid_columnconfigure(0, weight=1)

        row = 0
        ctk.CTkLabel(form, text="Profile", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.source_detail_account_menu = self._option_menu(
            form,
            self.source_detail_account_var,
            ["No accounts"],
        )
        self.source_detail_account_menu.grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))
        row += 2

        self.source_featured_switch = ctk.CTkSwitch(
            form,
            text="Featured channel",
            variable=self.source_featured_var,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 12),
        )
        self.source_featured_switch.grid(row=row, column=0, sticky="w", pady=(0, 12))
        row += 1

        ctk.CTkLabel(form, text="Channel name", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.source_name_entry = self._entry(form, self.source_name_var, "Example: Food Review")
        self.source_name_entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))
        row += 2

        ctk.CTkLabel(form, text="TikTok URL or @handle", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.source_url_entry = self._entry(form, self.source_url_var, "https://www.tiktok.com/@channel")
        self.source_url_entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))
        self.source_url_entry.bind("<Return>", lambda _event: self._save_source_channel())
        row += 2

        ctk.CTkLabel(form, text="Note", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.source_note_text = self._textbox(form, height=86)
        self.source_note_text.grid(row=row + 1, column=0, sticky="ew", pady=(0, 10))
        row += 2

        buttons = ctk.CTkFrame(detail_card, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=18, pady=(6, 0))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        self.source_save_button = ctk.CTkButton(buttons, text="Save", height=36, command=self._save_source_channel, **_button_kwargs("primary"))
        self.source_save_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.source_open_button = ctk.CTkButton(buttons, text="Open on Phone", height=36, command=self._open_selected_source_on_phone, **_button_kwargs("secondary"))
        self.source_open_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.source_delete_button = ctk.CTkButton(buttons, text="Delete", height=36, command=self._delete_source_channel, **_button_kwargs("danger"))
        self.source_delete_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.action_buttons.extend((self.source_save_button, self.source_open_button, self.source_delete_button))
        self._update_source_detail_buttons()

    def _build_telegram_bot_section(self, parent, row: int, column: int) -> None:
        card, body, actions = self._card(
            parent,
            "Telegram Bot",
            "Configure and control the Telegram processing bot.",
        )
        card.grid(row=row, column=column, sticky="nsew", padx=(7, 0), pady=(0, 0))
        self.telegram_add_bot_button = self._add_action_button(actions, "Add", self._show_telegram_add_bot_form, "secondary")
        self.telegram_bot_button = self._add_action_button(actions, "Start Bot", self._start_telegram_bot, "primary")
        self.telegram_pause_button = self._add_action_button(actions, "Pause", lambda: self._pause_telegram_bot(show_status=True), "secondary")
        self.telegram_stop_button = self._add_action_button(actions, "Stop", lambda: self._stop_telegram_bot(show_status=True, hard=True), "danger")
        self.telegram_add_bot_button.configure(width=54)
        self.telegram_bot_button.configure(width=78)
        self.telegram_pause_button.configure(width=62)
        self.telegram_stop_button.configure(width=58)

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.telegram_content_stack = ctk.CTkFrame(body, fg_color=COLORS["surface_2"], corner_radius=10)
        self.telegram_content_stack.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.telegram_content_stack.grid_columnconfigure(0, weight=1)
        self.telegram_content_stack.grid_rowconfigure(0, weight=1)

        self.telegram_video_settings_frame = ctk.CTkFrame(self.telegram_content_stack, fg_color="transparent")
        self.telegram_add_bot_frame = ctk.CTkFrame(self.telegram_content_stack, fg_color="transparent")
        for frame in (self.telegram_video_settings_frame, self.telegram_add_bot_frame):
            frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        self._build_video_edit_settings_content(self.telegram_video_settings_frame)
        self._build_telegram_add_bot_content(self.telegram_add_bot_frame)
        self.telegram_add_bot_frame.grid_remove()

        self._set_telegram_bot_button_running(False)
        self._update_telegram_target_profile_label()

    def _build_telegram_add_bot_content(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        title_stack = ctk.CTkFrame(header, fg_color="transparent")
        title_stack.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_stack,
            text="Add Telegram Bot",
            font=(FONT, 14, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_stack,
            text="Add a bot configuration to telegram_bots.json.",
            font=(FONT, 11),
            text_color=COLORS["muted"],
        ).pack(anchor="w", pady=(1, 0))

        name_frame = ctk.CTkFrame(parent, fg_color="transparent")
        name_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        name_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            name_frame,
            text="Name",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.telegram_bot_name_entry = self._entry(name_frame, self.telegram_bot_name_var, "Profile/bot name")
        self.telegram_bot_name_entry.grid(row=1, column=0, sticky="ew")

        token_frame = ctk.CTkFrame(parent, fg_color="transparent")
        token_frame.grid(row=2, column=0, sticky="ew", padx=(0, 7), pady=(0, 10))
        token_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            token_frame,
            text="Bot token",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._entry(token_frame, self.telegram_bot_token_var, "Telegram Bot Token").grid(row=1, column=0, sticky="ew")

        chat_frame = ctk.CTkFrame(parent, fg_color="transparent")
        chat_frame.grid(row=2, column=1, sticky="ew", padx=(7, 0), pady=(0, 10))
        chat_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            chat_frame,
            text="Chat ID",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._entry(chat_frame, self.telegram_chat_id_var, "Chat ID").grid(row=1, column=0, sticky="ew")

        buttons = ctk.CTkFrame(parent, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        self.telegram_add_cancel_button = ctk.CTkButton(
            buttons,
            text="Cancel",
            width=76,
            height=32,
            command=self._cancel_telegram_add_bot,
            **_button_kwargs("secondary"),
        )
        self.telegram_add_cancel_button.pack(side="left", padx=(0, 8))
        self.telegram_add_save_button = ctk.CTkButton(
            buttons,
            text="Save Bot",
            width=86,
            height=32,
            command=self._save_new_telegram_bot,
            **_button_kwargs("primary"),
        )
        self.telegram_add_save_button.pack(side="left")
        self.action_buttons.extend((self.telegram_add_cancel_button, self.telegram_add_save_button))

    def _build_phone_control_section(self, parent, row: int, column: int) -> None:
        card, body, actions = self._card(
            parent,
            "Phone Control",
            "Connect over ADB Wi-Fi. Dragged videos are added to DCIM/Camera for Gallery.",
        )
        card.grid(row=row, column=column, sticky="nsew", padx=(0, 7))
        self.phone_connect_button = self._add_action_button(
            actions,
            "Connect",
            self._connect_phone,
            "secondary",
        )
        self.phone_control_button = self._add_action_button(
            actions,
            "Control",
            self._start_phone_control,
            "primary",
        )
        self.phone_close_button = self._add_action_button(
            actions,
            "Close",
            self._stop_phone_control,
            "danger",
        )
        self.phone_connect_button.configure(width=82)
        self.phone_control_button.configure(width=82)
        self.phone_close_button.configure(width=58)

        body.grid_columnconfigure(0, weight=1)

        settings_card = ctk.CTkFrame(body, fg_color=COLORS["surface_2"], corner_radius=10)
        settings_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        settings_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            settings_card,
            text="Phone Settings",
            font=(FONT, 14, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        phone_line = ctk.CTkFrame(settings_card, fg_color="transparent")
        phone_line.grid(row=1, column=0, sticky="ew", padx=12)
        phone_line.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            phone_line,
            text="Phone IP",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.phone_address_entry = self._entry(phone_line, self.phone_ip_var, "192.168.1.20")
        self.phone_address_entry.grid(row=0, column=1, sticky="ew")
        self.phone_address_entry.bind("<Return>", lambda _event: self._connect_phone())
        ctk.CTkLabel(
            phone_line,
            text="Port",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=2, sticky="w", padx=(10, 8))
        self.phone_port_entry = self._entry(phone_line, self.phone_port_var, str(DEFAULT_ADB_PORT))
        self.phone_port_entry.configure(width=78)
        self.phone_port_entry.grid(row=0, column=3, sticky="w")
        self.phone_port_entry.bind("<Return>", lambda _event: self._connect_phone())
        for column, (text, variable) in enumerate(
            (
                ("Keep screen awake", self.phone_keep_screen_awake_var),
                ("Turn phone screen off", self.phone_turn_screen_off_var),
                ("Always on top", self.phone_always_on_top_var),
            ),
            start=4,
        ):
            ctk.CTkSwitch(
                phone_line,
                text=text,
                variable=variable,
                command=self._on_phone_control_settings_changed,
                progress_color=COLORS["accent"],
                button_color=COLORS["text"],
                text_color=COLORS["text"],
                font=(FONT, 11),
            ).grid(row=0, column=column, sticky="w", padx=(12, 0))

        quality = ctk.CTkFrame(settings_card, fg_color="transparent")
        quality.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 12))
        for index in range(5):
            quality.grid_columnconfigure(index, weight=1, uniform="phone_quality")
        for column, (label, variable, values, width) in enumerate(
            (
                ("FPS", self.phone_max_fps_var, ("30", "60"), 78),
                (
                    "Resolution",
                    self.phone_max_size_var,
                    ("1024", "1280", "1600"),
                    104,
                ),
                (
                    "Bitrate",
                    self.phone_video_bit_rate_var,
                    ("4 Mbps", "6 Mbps", "8 Mbps"),
                    112,
                ),
                (
                    "Screen",
                    self.phone_monitor_target_var,
                    ("Main", "Secondary"),
                    104,
                ),
                (
                    "Dock",
                    self.phone_dock_position_var,
                    ("Off", "Dock left", "Dock right"),
                    116,
                ),
            )
        ):
            item = ctk.CTkFrame(quality, fg_color="transparent")
            item.grid(row=0, column=column, sticky="ew", padx=(0, 8 if column < 4 else 0))
            ctk.CTkLabel(
                item,
                text=label,
                text_color=COLORS["muted"],
                font=(FONT, 10, "bold"),
            ).pack(side="left", padx=(0, 5))
            menu = self._option_menu(
                item,
                variable,
                values,
                command=lambda _value: self._on_phone_control_settings_changed(),
            )
            menu.configure(
                width=width,
                height=30,
                font=(FONT, 11),
                dropdown_font=(FONT, 11),
            )
            menu.pack(side="left")

        self._set_phone_control_running(False)

    def _build_live_console_section(self, parent, row: int, column: int) -> None:
        card, body, _actions = self._card(
            parent,
            "Live Console",
            "Real-time Telegram, phone control, and file transfer events.",
            compact=True,
        )
        self.live_console_card = card
        card.grid(row=row, column=column, columnspan=2, sticky="nsew", pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.telegram_event_log = ctk.CTkTextbox(
            body,
            height=80,
            fg_color="#080D18",
            border_color=COLORS["border"],
            border_width=1,
            text_color="#A7F3D0",
            corner_radius=8,
            font=("Consolas", 11),
            wrap="word",
        )
        self.telegram_event_log.grid(row=0, column=0, sticky="nsew")
        self.telegram_event_log.configure(state="disabled")
        self._append_telegram_event("Console ready")

    def _build_videos_tab(self) -> None:
        self.videos_tab.grid_columnconfigure(0, weight=1)
        self.videos_tab.grid_columnconfigure(1, weight=0, minsize=440)
        self.videos_tab.grid_rowconfigure(0, weight=1)
        table_card, table_body, table_actions = self._card(self.videos_tab, "Videos", "Select a row to edit publish details.")
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.video_filter_account_var = tk.StringVar(value="All accounts")
        self.video_filter_account_ids_by_label = {"All accounts": "all"}
        filter_frame = ctk.CTkFrame(table_actions, fg_color="transparent")
        filter_frame.grid(row=0, column=0, sticky="e", padx=(0, 8))
        ctk.CTkLabel(filter_frame, text="Account", text_color=COLORS["muted"], font=(FONT, 11, "bold")).pack(side="left", padx=(0, 6))
        self.video_filter_account_menu = self._option_menu(
            filter_frame,
            self.video_filter_account_var,
            ["All accounts"],
            command=lambda _value: self._on_video_filter_changed(),
        )
        self.video_filter_account_menu.configure(width=210)
        self.video_filter_account_menu.pack(side="left")

        self.video_normal_actions = ctk.CTkFrame(table_actions, fg_color="transparent")
        self.video_normal_actions.grid(row=0, column=1, sticky="e")
        self.video_auto_post_button = self._add_action_button(
            self.video_normal_actions,
            "Auto Post",
            self._auto_post_selected_video,
            "primary",
        )
        self.video_auto_post_button.configure(width=112)
        self.video_send_button = self._add_action_button(
            self.video_normal_actions,
            "Gửi",
            self._send_selected_video_to_phone,
            "secondary",
        )
        self.video_send_button.configure(width=88)
        self.video_delete_button = self._add_action_button(
            "Connect",
            self._connect_phone,
            "secondary",
        )
        self.phone_control_button = self._add_action_button(
            actions,
            "Control",
            self._start_phone_control,
            "primary",
        )
        self.phone_close_button = self._add_action_button(
            actions,
            "Close",
            self._stop_phone_control,
            "danger",
        )
        self.phone_connect_button.configure(width=82)
        self.phone_control_button.configure(width=82)
        self.phone_close_button.configure(width=58)

        body.grid_columnconfigure(0, weight=1)

        settings_card = ctk.CTkFrame(body, fg_color=COLORS["surface_2"], corner_radius=10)
        settings_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        settings_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            settings_card,
            text="Phone Settings",
            font=(FONT, 14, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        phone_line = ctk.CTkFrame(settings_card, fg_color="transparent")
        phone_line.grid(row=1, column=0, sticky="ew", padx=12)
        phone_line.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            phone_line,
            text="Phone IP",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.phone_address_entry = self._entry(phone_line, self.phone_ip_var, "192.168.1.20")
        self.phone_address_entry.grid(row=0, column=1, sticky="ew")
        self.phone_address_entry.bind("<Return>", lambda _event: self._connect_phone())
        ctk.CTkLabel(
            phone_line,
            text="Port",
            text_color=COLORS["muted"],
            font=(FONT, 12, "bold"),
            anchor="w",
        ).grid(row=0, column=2, sticky="w", padx=(10, 8))
        self.phone_port_entry = self._entry(phone_line, self.phone_port_var, str(DEFAULT_ADB_PORT))
        self.phone_port_entry.configure(width=78)
        self.phone_port_entry.grid(row=0, column=3, sticky="w")
        self.phone_port_entry.bind("<Return>", lambda _event: self._connect_phone())
        for column, (text, variable) in enumerate(
            (
                ("Keep screen awake", self.phone_keep_screen_awake_var),
                ("Turn phone screen off", self.phone_turn_screen_off_var),
                ("Always on top", self.phone_always_on_top_var),
            ),
            start=4,
        ):
            ctk.CTkSwitch(
                phone_line,
                text=text,
                variable=variable,
                command=self._on_phone_control_settings_changed,
                progress_color=COLORS["accent"],
                button_color=COLORS["text"],
                text_color=COLORS["text"],
                font=(FONT, 11),
            ).grid(row=0, column=column, sticky="w", padx=(12, 0))

        quality = ctk.CTkFrame(settings_card, fg_color="transparent")
        quality.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 12))
        for index in range(5):
            quality.grid_columnconfigure(index, weight=1, uniform="phone_quality")
        for column, (label, variable, values, width) in enumerate(
            (
                ("FPS", self.phone_max_fps_var, ("30", "60"), 78),
                (
                    "Resolution",
                    self.phone_max_size_var,
                    ("1024", "1280", "1600"),
                    104,
                ),
                (
                    "Bitrate",
                    self.phone_video_bit_rate_var,
                    ("4 Mbps", "6 Mbps", "8 Mbps"),
                    112,
                ),
                (
                    "Screen",
                    self.phone_monitor_target_var,
                    ("Main", "Secondary"),
                    104,
                ),
                (
                    "Dock",
                    self.phone_dock_position_var,
                    ("Off", "Dock left", "Dock right"),
                    116,
                ),
            )
        ):
            item = ctk.CTkFrame(quality, fg_color="transparent")
            item.grid(row=0, column=column, sticky="ew", padx=(0, 8 if column < 4 else 0))
            ctk.CTkLabel(
                item,
                text=label,
                text_color=COLORS["muted"],
                font=(FONT, 10, "bold"),
            ).pack(side="left", padx=(0, 5))
            menu = self._option_menu(
                item,
                variable,
                values,
                command=lambda _value: self._on_phone_control_settings_changed(),
            )
            menu.configure(
                width=width,
                height=30,
                font=(FONT, 11),
                dropdown_font=(FONT, 11),
            )
            menu.pack(side="left")

        self._set_phone_control_running(False)

    def _build_live_console_section(self, parent, row: int, column: int) -> None:
        card, body, _actions = self._card(
            parent,
            "Live Console",
            "Real-time Telegram, phone control, and file transfer events.",
            compact=True,
        )
        self.live_console_card = card
        card.grid(row=row, column=column, columnspan=2, sticky="nsew", pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.telegram_event_log = ctk.CTkTextbox(
            body,
            height=80,
            fg_color="#080D18",
            border_color=COLORS["border"],
            border_width=1,
            text_color="#A7F3D0",
            corner_radius=8,
            font=("Consolas", 11),
            wrap="word",
        )
        self.telegram_event_log.grid(row=0, column=0, sticky="nsew")
        self.telegram_event_log.configure(state="disabled")
        self._append_telegram_event("Console ready")

    def _build_videos_tab(self) -> None:
        self.videos_tab.grid_columnconfigure(0, weight=1)
        self.videos_tab.grid_columnconfigure(1, weight=0, minsize=440)
        self.videos_tab.grid_rowconfigure(0, weight=1)
        table_card, table_body, table_actions = self._card(self.videos_tab, "Videos", "Select a row to edit publish details.")
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.video_filter_account_var = tk.StringVar(value="All accounts")
        self.video_filter_account_ids_by_label = {"All accounts": "all"}
        filter_frame = ctk.CTkFrame(table_actions, fg_color="transparent")
        filter_frame.grid(row=0, column=0, sticky="e", padx=(0, 8))
        ctk.CTkLabel(filter_frame, text="Account", text_color=COLORS["muted"], font=(FONT, 11, "bold")).pack(side="left", padx=(0, 6))
        self.video_filter_account_menu = self._option_menu(
            filter_frame,
            self.video_filter_account_var,
            ["All accounts"],
            command=lambda _value: self._on_video_filter_changed(),
        )
        self.video_filter_account_menu.configure(width=210)
        self.video_filter_account_menu.pack(side="left")

        self.video_normal_actions = ctk.CTkFrame(table_actions, fg_color="transparent")
        self.video_normal_actions.grid(row=0, column=1, sticky="e")
        self.video_auto_post_button = self._add_action_button(
            self.video_normal_actions,
            "Auto Post",
            self._auto_post_selected_video,
            "primary",
        )
        self.video_auto_post_button.configure(width=112)
        self.video_send_button = self._add_action_button(
            self.video_normal_actions,
            "Gửi",
            self._send_selected_video_to_phone,
            "secondary",
        )
        self.video_send_button.configure(width=88)
        self.video_delete_button = self._add_action_button(
            self.video_normal_actions,
            "Xóa",
            self._delete_current_video,
            "danger",
        )
        self.video_delete_button.configure(width=80)

        columns = (
            "row_no",
            "action",
            "play",
            "account_name",
            "cut_mode",
            "file_path",
            "caption",
            "hashtags",
            "product_id",
            "publish_mode",
            "scheduled_at",
            "source",
            "updated_at",
        )
        self.video_normal_columns = (
            "row_no",
            "account_name",
            "cut_mode",
            "caption",
            "updated_at",
            "hashtags",
            "action",
            "play",
        )
        self.video_table = CTkDataTable(
            table_body,
            columns,
            (
                ("row_no", "#", 70),
                ("action", "Action", 100),
                ("play", "Play", 50),
                ("account_name", "Profile", 200),
                ("cut_mode", "Cut Mode", 170),
                ("file_path", "Video File", 320),
                ("caption", "Description", 500),
                ("hashtags", "Hashtags", 280),
                ("product_id", "Product ID", 170),
                ("publish_mode", "Mode", 120),
                ("scheduled_at", "Scheduled", 220),
                ("source", "Source", 90),
                ("updated_at", "Updated", 180),
            ),
            on_select=self._on_video_selected,
            on_cell_click=self._on_video_cell_clicked,
        )
        self.video_table.configure(displaycolumns=self.video_normal_columns)
        self.video_table.pack(fill="both", expand=True)
        self.video_table.set_column_alignment("row_no", tk.CENTER)
        self.video_table.set_column_alignment("play", tk.CENTER)
        self.video_table.bind("<Motion>", self._on_video_table_motion, add="+")
        self.video_table.bind("<Leave>", self._on_video_table_leave, add="+")

        detail_card = ctk.CTkFrame(self.videos_tab, width=440, fg_color=COLORS["surface"], corner_radius=14)
        detail_card.grid(row=0, column=1, sticky="nsew")
        detail_card.grid_propagate(False)
        detail_card.grid_columnconfigure(0, weight=1)
        detail_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            detail_card,
            text="Video workspace",
            font=(FONT, 16, "bold"),
            text_color=COLORS["text"],
            anchor="w",
            height=24,
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        detail_body = ctk.CTkFrame(detail_card, fg_color="transparent")
        detail_body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._build_video_workspace(detail_body)

    def _build_video_workspace(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        detail = ctk.CTkFrame(parent, fg_color="transparent")
        detail.grid(row=0, column=0, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(1, weight=1)
        self.video_detail_id_var = tk.StringVar(value="-")
        self.video_detail_profile_var = tk.StringVar(value="")
        self.video_detail_file_var = tk.StringVar(value="")
        self.video_detail_source_var = tk.StringVar(value="")
        self.video_detail_status_var = tk.StringVar(value="")
        self.video_detail_updated_var = tk.StringVar(value="")
        self.video_detail_account_var = tk.StringVar(value="")
        self.video_detail_product_var = tk.StringVar(value="")
        self.video_detail_publish_mode_var = tk.StringVar(value="now")
        self.video_detail_scheduled_var = tk.StringVar(value="")
        for variable in (
            self.video_detail_account_var,
            self.video_detail_product_var,
            self.video_detail_publish_mode_var,
            self.video_detail_scheduled_var,
        ):
            variable.trace_add("write", self._on_video_detail_field_changed)

        meta = ctk.CTkFrame(detail, fg_color=COLORS["surface_2"], corner_radius=12)
        meta.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        meta.configure(height=226)
        meta.grid_propagate(False)
        meta.grid_columnconfigure(0, minsize=72)
        meta.grid_columnconfigure(1, weight=1)
        for index, (label, variable) in enumerate(
            (
                ("No.", self.video_detail_id_var),
                ("Profile", self.video_detail_profile_var),
                ("Status", self.video_detail_status_var),
                ("Source", self.video_detail_source_var),
                ("Updated", self.video_detail_updated_var),
                ("File", self.video_detail_file_var),
            )
        ):
            ctk.CTkLabel(
                meta,
                text=label,
                text_color=COLORS["muted"],
                font=(FONT, 11, "bold"),
                anchor="w",
                width=68,
            ).grid(row=index, column=0, sticky="w", padx=(12, 8), pady=(8 if index == 0 else 3, 8 if index == 5 else 3))
            value_label = ctk.CTkLabel(
                meta,
                textvariable=variable,
                text_color=COLORS["text"],
                font=(FONT, 12),
                anchor="w",
                justify="left",
                wraplength=270,
            )
            value_label.grid(row=index, column=1, sticky="ew", padx=(0, 12), pady=(8 if index == 0 else 3, 8 if index == 5 else 3))
            if label == "File":
                self.video_detail_file_label = value_label
                value_label.bind("<Enter>", self._on_video_file_label_enter)
                value_label.bind("<Leave>", self._on_video_file_label_leave)
                value_label.bind("<Button-1>", self._open_selected_video_folder)

        form = ctk.CTkFrame(detail, fg_color="transparent")
        form.grid(row=1, column=0, sticky="nsew")
        form.grid_columnconfigure(0, weight=1)

        row = 0
        detail_text_height = 78
        ctk.CTkLabel(form, text="Account", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w", height=18).grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.video_detail_account_combo = self._option_menu(
            form,
            variable=self.video_detail_account_var,
            values=["No accounts"],
        )
        self.video_detail_account_combo.grid(row=row + 1, column=0, sticky="ew", pady=(0, 9))
        row += 2

        description_header = ctk.CTkFrame(form, fg_color="transparent")
        description_header.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        description_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(description_header, text="Description", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w", height=18).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            description_header,
            text="⧉",
            width=38,
            height=26,
            command=self._copy_video_detail_caption_with_hashtags,
            **_button_kwargs("secondary"),
        ).grid(row=0, column=1, sticky="e")
        self.video_detail_caption_text = self._textbox(form, height=detail_text_height)
        form.grid_rowconfigure(row + 1, weight=0, minsize=detail_text_height)
        self.video_detail_caption_text.grid(row=row + 1, column=0, sticky="ew", pady=(0, 9))
        self.video_detail_caption_text.bind("<KeyRelease>", lambda _event: self._on_video_detail_limited_text_modified(self.video_detail_caption_text))
        row += 2

        ctk.CTkLabel(form, text="Hashtags", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w", height=18).grid(row=row, column=0, sticky="ew", pady=(0, 4))
        self.video_detail_hashtags_input = YouTubeTagInput(form, on_change=lambda _hashtags: self._on_video_detail_text_modified(), height=112)
        form.grid_rowconfigure(row + 1, weight=0, minsize=112)
        self.video_detail_hashtags_input.grid(row=row + 1, column=0, sticky="ew", pady=(0, 9))
        row += 2

        two_col = ctk.CTkFrame(form, fg_color="transparent")
        two_col.grid(row=row, column=0, sticky="ew", pady=(0, 9))
        two_col.grid_columnconfigure(0, weight=0, minsize=166)
        two_col.grid_columnconfigure(1, weight=0, minsize=40)
        two_col.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(two_col, text="Product ID", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 5))
        ctk.CTkLabel(two_col, text="Mode", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=(0, 5))
        self._entry(two_col, self.video_detail_product_var, "Product ID").grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.video_detail_product_send_button = ctk.CTkButton(
            two_col,
            text=SEND_ICON,
            width=36,
            height=32,
            command=self._send_video_detail_product_id_to_phone_clipboard,
            **_button_kwargs("secondary"),
        )
        self.video_detail_product_send_button.grid(row=1, column=1, sticky="e", padx=(0, 8))
        self.action_buttons.append(self.video_detail_product_send_button)
        self._option_menu(
            two_col,
            variable=self.video_detail_publish_mode_var,
            values=list(PUBLISH_MODES),
        ).grid(row=1, column=2, sticky="ew", padx=(8, 0))
        row += 1

        ctk.CTkLabel(form, text="Scheduled", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w", height=18).grid(row=row, column=0, sticky="ew", pady=(0, 4))
        schedule_frame = ctk.CTkFrame(form, fg_color="transparent")
        schedule_frame.grid(row=row + 1, column=0, sticky="ew", pady=(0, 9))
        schedule_frame.grid_columnconfigure(0, weight=1)
        self._entry(schedule_frame, self.video_detail_scheduled_var, "YYYY-MM-DD HH:MM").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(schedule_frame, text="Pick", width=70, command=self._pick_video_schedule, **_button_kwargs("secondary")).grid(row=0, column=1, padx=(8, 0))
        row += 2

    def _build_activity_tab(self) -> None:
        self.activity_tab.grid_columnconfigure(0, weight=1)
        self.activity_tab.grid_rowconfigure(0, weight=1)
        card, body, actions = self._card(
            self.activity_tab,
            "System Activity",
            "Account, Telegram, phone transfer, Gallery, and automation events.",
        )
        card.grid(row=0, column=0, sticky="nsew")
        self._add_action_button(actions, "Refresh", self._refresh_logs, "secondary")
        self._add_action_button(actions, "Clear logs", self._clear_logs, "danger")
        self.log_table = CTkDataTable(
            body,
            ("id", "created_at", "level", "action", "account_id", "video_id", "message"),
            (
                ("id", "ID", 60),
                ("created_at", "Created", 170),
                ("level", "Level", 80),
                ("action", "Action", 160),
                ("account_id", "Account", 80),
                ("video_id", "Video", 80),
                ("message", "Message", 520),
            ),
        )
        self.log_table.configure(displaycolumns=("id", "created_at", "level", "action", "message"))
        self.log_table.pack(fill="both", expand=True)

    def _add_action_button(self, parent, text: str, command, kind: str = "secondary"):
        button = ctk.CTkButton(parent, text=text, height=34, command=command, **_button_kwargs(kind))
        column = len(parent.grid_slaves(row=0))
        button.grid(row=0, column=column, padx=(8, 0))
        self.action_buttons.append(button)
        return button

    def _show_tab_name(self, name: str) -> None:
        self._show_tab(self.tab_by_name[name])
        for key, button in self.nav_buttons.items():
            if key == name:
                button.configure(fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], text_color="#FFFFFF")
            else:
                button.configure(**_button_kwargs("secondary"))

    def _show_tab(self, tab) -> None:
        tab.tkraise()
        for key, frame in getattr(self, "tab_by_name", {}).items():
            if frame is tab:
                for button_key, button in self.nav_buttons.items():
                    button.configure(**(_button_kwargs("primary") if button_key == key else _button_kwargs("secondary")))
                break

    def _refresh_accounts(self) -> None:
        if getattr(self, "account_hashtags_editor", None) is not None:
            return
        selected_id = self._selected_account_id()
        self.account_table.delete(*self.account_table.get_children())
        accounts = self.manager.list_accounts()
        self.account_count_var.set(str(len(accounts)))
        for account in accounts:
            self.account_table.insert(
                "",
                tk.END,
                iid=str(account.id),
                tags=(self._status_tag(account.status),),
                values=(
                    account.id,
                    account.name,
                    account.bot_name,
                    account.login_type,
                    account.status,
                    self._account_cut_mode_label(account),
                    account.hashtags,
                    account.profile_path,
                    account.note,
                    _format_vietnam_datetime(account.updated_at),
                ),
            )
        if selected_id is not None and self.account_table.exists(str(selected_id)):
            self.account_table.selection_set(str(selected_id))
        self._refresh_source_account_options(accounts)

    def _refresh_source_account_options(self, accounts=None) -> None:
        if not hasattr(self, "source_account_menu"):
            return
        accounts = list(accounts if accounts is not None else self.manager.list_accounts())
        if not accounts:
            labels = ["No accounts"]
            mapping = {"No accounts": None}
        else:
            labels = ["%s - %s" % (account.id, account.name) for account in accounts]
            mapping = {"%s - %s" % (account.id, account.name): account.id for account in accounts}
        current = self.source_account_var.get()
        current_detail = self.source_detail_account_var.get()
        self.source_account_ids_by_label = mapping
        self.source_account_menu.configure(values=labels)
        if hasattr(self, "source_detail_account_menu"):
            self.source_detail_account_menu.configure(values=labels)
        if current not in mapping:
            self.source_account_var.set(labels[0])
            self._clear_source_detail()
        elif current_detail not in mapping:
            self.source_detail_account_var.set(current)

    def _source_selected_account_id(self) -> int | None:
        return self.source_account_ids_by_label.get(self.source_account_var.get())

    def _source_detail_account_id(self) -> int | None:
        return self.source_account_ids_by_label.get(self.source_detail_account_var.get())

    def _source_account_label_for_id(self, account_id: int | None) -> str:
        for label, mapped_id in self.source_account_ids_by_label.items():
            if mapped_id == account_id:
                return label
        return self.source_account_var.get()

    def _refresh_sources(self) -> None:
        if not hasattr(self, "source_table"):
            return
        selected_id = self._selected_source_id()
        account_id = self._source_selected_account_id()
        self.source_table.delete(*self.source_table.get_children())
        channels = []
        if account_id is not None:
            channels = self.manager.list_source_channels(account_id)
        for row_number, channel in enumerate(channels, start=1):
            self.source_table.insert(
                "",
                tk.END,
                iid=str(channel.id),
                tags=("live",),
                values=(
                    row_number,
                    SOURCE_FEATURED_LABEL if channel.featured else "",
                    SOURCE_OPEN_LABEL,
                    channel.name,
                    channel.url,
                    channel.note,
                    _format_vietnam_datetime(channel.updated_at),
                ),
            )
        self.source_snapshot = self._source_snapshot(channels)
        if selected_id is not None and self.source_table.exists(str(selected_id)):
            self.source_table.selection_set(str(selected_id))
        else:
            self._update_source_detail_buttons()

    def _on_source_account_changed(self) -> None:
        self._clear_source_detail()
        self._refresh_sources()

    def _on_source_selected(self, _event=None) -> None:
        source_id = self._selected_source_id()
        if source_id is None:
            self._update_source_detail_buttons()
            return
        channel = self.manager.get_source_channel(source_id)
        if channel is None:
            self._clear_source_detail()
            self._refresh_sources()
            return
        self.source_editing_id = channel.id
        self.source_detail_account_var.set(self._source_account_label_for_id(channel.account_id))
        self.source_name_var.set(channel.name)
        self.source_url_var.set(channel.url)
        self.source_featured_var.set(bool(channel.featured))
        self.source_note_text.delete("1.0", tk.END)
        self.source_note_text.insert("1.0", channel.note)
        self._update_source_detail_buttons()

    def _on_source_cell_click(self, row_id: str, column: str) -> None:
        if column == "featured":
            self._toggle_source_featured(int(row_id))
        elif column == "open":
            self._open_source_on_phone(int(row_id))

    def _toggle_source_featured(self, channel_id: int) -> None:
        channel = self.manager.get_source_channel(channel_id)
        if channel is None:
            messagebox.showerror("Source missing", "The selected source channel no longer exists.")
            self._refresh_sources()
            return
        try:
            updated = self.manager.set_source_channel_featured(channel.id, not channel.featured)
            self.manager.add_log(
                "info",
                "source_featured",
                "Marked source channel %s as %s." % (updated.name, "featured" if updated.featured else "normal"),
                account_id=updated.account_id,
            )
        except Exception as exc:
            messagebox.showerror("Update source failed", str(exc))
            return
        self._refresh_sources()
        if self.source_table.exists(str(updated.id)):
            self.source_table.selection_set(str(updated.id))
        self._refresh_logs()
        self.status_var.set(
            "Featured source: %s" % updated.name if updated.featured else "Source no longer featured: %s" % updated.name
        )

    def _new_source_channel(self) -> None:
        self.source_table.tree.selection_set(())
        self._clear_source_detail()
        self.source_name_entry.focus_set()

    def _clear_source_detail(self) -> None:
        self.source_editing_id = None
        self.source_detail_account_var.set(self.source_account_var.get())
        self.source_name_var.set("")
        self.source_url_var.set("")
        self.source_featured_var.set(False)
        if hasattr(self, "source_note_text"):
            self.source_note_text.delete("1.0", tk.END)
        self._update_source_detail_buttons()

    def _selected_source_id(self) -> int | None:
        if not hasattr(self, "source_table"):
            return None
        selection = self.source_table.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _selected_source_channel(self):
        source_id = self._selected_source_id()
        if source_id is None:
            return None
        return self.manager.get_source_channel(source_id)

    def _source_note_value(self) -> str:
        if not hasattr(self, "source_note_text"):
            return ""
        return self.source_note_text.get("1.0", tk.END).strip()

    def _save_source_channel(self) -> None:
        account_id = self._source_detail_account_id()
        if account_id is None:
            messagebox.showinfo("Select profile", "Create or select a profile first.")
            return
        try:
            if self.source_editing_id is None:
                channel = self.manager.add_source_channel(
                    account_id=account_id,
                    name=self.source_name_var.get(),
                    url=self.source_url_var.get(),
                    note=self._source_note_value(),
                    featured=bool(self.source_featured_var.get()),
                    enabled=True,
                )
                self.manager.add_log("info", "source_added", "Added source channel %s." % channel.name, account_id=account_id)
            else:
                channel = self.manager.update_source_channel(
                    channel_id=self.source_editing_id,
                    account_id=account_id,
                    name=self.source_name_var.get(),
                    url=self.source_url_var.get(),
                    note=self._source_note_value(),
                    featured=bool(self.source_featured_var.get()),
                    enabled=True,
                )
                self.manager.add_log("info", "source_updated", "Updated source channel %s." % channel.name, account_id=account_id)
        except Exception as exc:
            messagebox.showerror("Save source failed", str(exc))
            return
        self.source_editing_id = channel.id
        self.source_account_var.set(self._source_account_label_for_id(channel.account_id))
        self.source_detail_account_var.set(self._source_account_label_for_id(channel.account_id))
        self._refresh_sources()
        self.source_table.selection_set(str(channel.id))
        self._refresh_logs()
        self.status_var.set("Saved source channel: %s" % channel.name)

    def _delete_source_channel(self) -> None:
        channel = self._selected_source_channel()
        if channel is None:
            messagebox.showinfo("Select source", "Select a source channel first.")
            return
        if not messagebox.askyesno("Delete source", "Delete source channel %s?" % channel.name):
            return
        self.manager.delete_source_channel(channel.id)
        self.manager.add_log("info", "source_deleted", "Deleted source channel %s." % channel.name, account_id=channel.account_id)
        self._clear_source_detail()
        self._refresh_sources()
        self._refresh_logs()
        self.status_var.set("Deleted source channel: %s" % channel.name)

    def _open_selected_source_on_phone(self) -> None:
        channel = self._selected_source_channel()
        if channel is None:
            messagebox.showinfo("Select source", "Select a source channel first.")
            return
        self._open_source_on_phone(channel.id)

    def _open_source_on_phone(self, channel_id: int) -> None:
        channel = self.manager.get_source_channel(channel_id)
        if channel is None:
            messagebox.showerror("Source missing", "The selected source channel no longer exists.")
            self._refresh_sources()
            return

        def worker():
            runner = self.phone_controller.runner
            runner.ensure_tool(self.config.adb_bin)
            target = self._resolve_source_phone_target()
            package_name = self._installed_tiktok_package(target)
            forced = None
            if package_name:
                forced = runner.run(
                    [
                        self.config.adb_bin,
                        "-s",
                        target,
                        "shell",
                        "am",
                        "start",
                        "-a",
                        "android.intent.action.VIEW",
                        "-d",
                        channel.url,
                        "-p",
                        package_name,
                    ],
                    check=False,
                )
                if forced.returncode == 0:
                    return channel
            fallback = runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    channel.url,
                ],
                check=False,
            )
            if fallback.returncode != 0:
                detail = (fallback.stderr or (forced.stderr if forced is not None else "") or "").strip()
                raise RuntimeError(detail or "Could not open source channel on phone.")
            return channel

        def on_success(opened_channel) -> None:
            self.manager.add_log(
                "info",
                "source_open_phone",
                "Opened source channel on phone: %s." % opened_channel.url,
                account_id=opened_channel.account_id,
            )
            self._refresh_logs()
            self.status_var.set("Opened on phone: %s" % opened_channel.name)

        self._run_worker("Opening source channel on phone...", worker, on_success=on_success, error_title="Open source")

    def _resolve_source_phone_target(self) -> str:
        address = self._sync_phone_address_from_parts()
        device_serials = self._adb_device_serials()
        if address:
            candidates = []
            candidates.append(address)
            try:
                candidates.append(normalize_phone_address(address))
            except Exception:
                pass
            host = address.rsplit(":", 1)[0] if ":" in address else address
            for candidate in candidates:
                if candidate in device_serials:
                    return candidate
            host_matches = [serial for serial in device_serials if serial == host or serial.startswith("%s:" % host)]
            if len(host_matches) == 1:
                return host_matches[0]
            if not device_serials:
                raise RuntimeError("No ADB device is connected. Connect Phone Control first.")
            raise RuntimeError("Phone address %s is not an online ADB device." % address)
        if len(device_serials) == 1:
            return device_serials[0]
        if not device_serials:
            raise RuntimeError("No ADB device is connected. Connect Phone Control first.")
        raise RuntimeError("Multiple ADB devices are connected. Enter the phone IP:PORT in Phone Control first.")

    def _adb_device_serials(self) -> list[str]:
        completed = self.phone_controller.runner.run([self.config.adb_bin, "devices"], check=False)
        serials = []
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            if "\tdevice" in line:
                serials.append(line.split("\t", 1)[0].strip())
        return serials

    def _installed_tiktok_package(self, target: str) -> str:
        for package_name in TIKTOK_ANDROID_PACKAGES:
            completed = self.phone_controller.runner.run(
                [
                    self.config.adb_bin,
                    "-s",
                    target,
                    "shell",
                    "pm",
                    "path",
                    package_name,
                ],
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return package_name
        return ""

    def _update_source_detail_buttons(self) -> None:
        if not hasattr(self, "source_delete_button"):
            return
        selected = self._selected_source_id() is not None
        self.source_delete_button.configure(state="normal" if selected else "disabled")
        self.source_open_button.configure(state="normal" if selected else "disabled")

    def _video_action_label(self, video) -> str:
        if self._is_video_rendering(video):
            return "⏳ Đang tạo"
        if self._is_video_waiting_to_render(video):
            return "⏳ Đang chờ"
        if self._is_video_render_draft(video):
            return "↻ Tạo lại" if video.status == "error" else "＋ Tạo"
        if self._video_final_path_exists(video):
            return "↻ Tạo lại"
        return "＋ Tạo"

    def _video_play_label(self, video) -> str:
        return "▶" if self._video_final_path_exists(video) else "▷"

    def _is_video_render_draft(self, video) -> bool:
        if video is None:
            return False
        has_inputs = bool(
            str(getattr(video, "source_video_url", "") or "").strip()
            and str(getattr(video, "product_image_path", "") or "").strip()
        )
        return has_inputs and not self._video_final_path_exists(video)

    def _is_video_waiting_to_render(self, video) -> bool:
        return bool(video and video.status == "queued")

    def _is_video_rendering(self, video) -> bool:
        return bool(video and video.status == "rendering")

    def _video_final_path_exists(self, video) -> bool:
        if video is None:
            return False
        try:
            path = self.manager.resolve_video_path(video)
        except Exception:
            return False
        return path.exists() and path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

    def _refresh_videos(self) -> None:
        selected_id = self._selected_video_id()
        accounts = self.manager.list_accounts()
        account_names = {account.id: account.name for account in accounts}
        videos = self.manager.list_videos()
        self.video_count_var.set(str(len(videos)))
        self._refresh_video_filter_options(accounts, videos)
        visible_videos = self._filter_videos_by_account(videos)
        self.video_table.delete(*self.video_table.get_children())
        for row_number, video in enumerate(visible_videos, start=1):
            self.video_table.insert(
                "",
                tk.END,
                iid=str(video.id),
                tags=(self._status_tag(video.status),),
                values=(
                    row_number,
                    self._video_action_label(video),
                    self._video_play_label(video),
                    account_names.get(video.account_id, ""),
                    self._video_row_cut_mode_label(video),
                    video.file_path,
                    (video.caption.replace("\n", " ")[:70] + "...") if video.caption and len(video.caption) > 70 else (video.caption.replace("\n", " ") if video.caption else video.caption),
                    video.hashtags,
                    video.product_id,
                    video.publish_mode,
                    _format_vietnam_datetime(video.scheduled_at, assume_utc=False),
                    video.source,
                    _format_vietnam_datetime(video.updated_at),
                ),
            )
        if selected_id is not None and self.video_table.exists(str(selected_id)):
            self.video_table.selection_set(str(selected_id))
        elif selected_id is not None:
            self.video_table.selection_set(())
        self._refresh_video_account_options()
        self._load_video_detail(self._current_selected_video())
        self.video_snapshot = self._video_snapshot(videos)

    def _refresh_video_filter_options(self, accounts=None, videos=None) -> None:
        if not hasattr(self, "video_filter_account_menu"):
            return
        accounts = list(accounts if accounts is not None else self.manager.list_accounts())
        videos = list(videos if videos is not None else self.manager.list_videos())
        labels = ["All accounts"]
        mapping = {"All accounts": "all"}
        if any(video.account_id is None for video in videos):
            labels.append("Unassigned")
            mapping["Unassigned"] = None
        for account in accounts:
            label = "%s - %s" % (account.id, account.name)
            labels.append(label)
            mapping[label] = account.id
        current = self.video_filter_account_var.get()
        self.video_filter_account_ids_by_label = mapping
        self.video_filter_account_menu.configure(values=labels)
        if current not in mapping:
            self.video_filter_account_var.set("All accounts")

    def _filter_videos_by_account(self, videos) -> list:
        selected = getattr(self, "video_filter_account_var", tk.StringVar(value="All accounts")).get()
        account_id = getattr(self, "video_filter_account_ids_by_label", {}).get(selected, "all")
        if account_id == "all":
            return list(videos)
        return [video for video in videos if video.account_id == account_id]

    def _on_video_filter_changed(self) -> None:
        self._refresh_videos()

    def _refresh_video_account_options(self) -> None:
        self.video_account_ids_by_label = {}
        labels = []
        for account in self.manager.list_accounts():
            label = "%s - %s" % (account.id, account.name)
            self.video_account_ids_by_label[label] = account.id
            labels.append(label)
        if not labels:
            labels = ["No accounts"]
            self.video_account_ids_by_label["No accounts"] = None
        if hasattr(self, "video_detail_account_combo"):
            self.video_detail_account_combo.configure(values=labels)

    def _on_video_selected(self, _event=None) -> None:
        if self.video_detail_restoring_selection:
            return
        previous_video_id = self.video_detail_video_id
        if not self._flush_video_detail_autosave(show_errors=False):
            if previous_video_id is not None and self.video_table.exists(str(previous_video_id)):
                self.video_detail_restoring_selection = True
                try:
                    self.video_table.selection_set(str(previous_video_id))
                finally:
                    self.video_detail_restoring_selection = False
            self.status_var.set("Auto save failed. Fix the video details before switching videos.")
            return
        self._load_video_detail(self._current_selected_video())

    def _on_video_cell_clicked(self, row_id: str, column: str) -> None:
        if column == "action":
            self._handle_video_action(row_id)
        elif column == "play":
            self._handle_video_play(row_id)
        elif column == "cut_mode":
            self._show_video_cut_mode_menu(row_id)

    def _handle_video_action(self, row_id: str) -> None:
        try:
            video_id = int(row_id)
        except (TypeError, ValueError):
            return
        video = self.manager.get_video(video_id)
        if video is None:
            return
        if self._is_video_waiting_to_render(video) or self._is_video_rendering(video):
            return
        self._queue_video_render(video)

    def _handle_video_play(self, row_id: str) -> None:
        try:
            video_id = int(row_id)
        except (TypeError, ValueError):
            return
        video = self.manager.get_video(video_id)
        if video is None:
            return
        if self._video_final_path_exists(video):
            self._play_video(row_id)

    def _play_video(self, row_id: str) -> None:
        try:
            video_id = int(row_id)
        except (TypeError, ValueError):
            return
        video = self.manager.get_video(video_id)
        if video is None:
            return
        video_path = self.manager.resolve_video_path(video)
        if not video_path.exists() or not video_path.is_file():
            messagebox.showerror("Play video", "Video file does not exist: %s" % video_path)
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(video_path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(video_path.as_uri())
            self.status_var.set("Playing video %s." % video.id)
        except Exception as exc:
            messagebox.showerror("Play video failed", str(exc))

    def _queue_video_render(self, video) -> None:
        if not self._flush_video_detail_autosave(show_errors=True):
            return
        fresh_video = self.manager.get_video(video.id)
        if fresh_video is None:
            messagebox.showerror("Create video", "Video không còn tồn tại.")
            return
        if fresh_video.id in self.video_render_pending_ids:
            self.status_var.set("Video %s đang nằm trong hàng chờ tạo." % fresh_video.id)
            return
        has_inputs = bool(
            str(getattr(fresh_video, "source_video_url", "") or "").strip()
            and str(getattr(fresh_video, "product_image_path", "") or "").strip()
        )
        if not has_inputs:
            messagebox.showerror("Create video", "Video %s thiếu link nguồn hoặc ảnh sản phẩm để tạo." % fresh_video.id)
            return

        if self._video_final_path_exists(fresh_video):
            try:
                path = self.manager.resolve_video_path(fresh_video)
                if path.exists():
                    path.unlink()
            except Exception as exc:
                messagebox.showerror("Create video", f"Không thể xóa video cũ: {exc}")
                return
        try:
            self.manager.update_video_status(fresh_video.id, "queued", note=fresh_video.note)
            self.manager.add_log("info", "video_render_queued", "Queued video %s for rendering." % fresh_video.id, account_id=fresh_video.account_id, video_id=fresh_video.id)
        except Exception as exc:
            messagebox.showerror("Create video", str(exc))
            return
        self.video_render_pending_ids.add(fresh_video.id)
        self._refresh_videos()
        self._refresh_logs()
        self.status_var.set("Đã đưa video %s vào hàng chờ tạo." % fresh_video.id)
        future = self.video_render_executor.submit(self._render_video_worker, fresh_video.id)
        future.add_done_callback(lambda completed, video_id=fresh_video.id: self._queue_video_render_result(video_id, completed))

    def _queue_video_render_result(self, video_id: int, future) -> None:
        try:
            result = future.result()
            self.events.put(("video_render_success", result, None, "Create video"))
        except Exception as exc:
            self.events.put(("video_render_error", {"video_id": video_id, "error": exc}, None, "Create video"))

    def _render_video_worker(self, video_id: int) -> dict:
        video = self.manager.get_video(video_id)
        if video is None:
            raise ValueError("Video not found: %s" % video_id)
        source_video_url = str(getattr(video, "source_video_url", "") or "").strip()
        product_image_path = self._resolve_project_path(getattr(video, "product_image_path", "") or "")
        if not source_video_url:
            raise ValueError("Video %s missing source video URL." % video_id)
        if product_image_path is None or not product_image_path.exists() or not product_image_path.is_file():
            raise ValueError("Video %s missing product image: %s" % (video_id, getattr(video, "product_image_path", "")))
        cut_mode = str(getattr(video, "cut_mode", "") or "original").strip().lower()
        if cut_mode not in VIDEO_ROW_CUT_MODE_LABELS:
            cut_mode = "original"
        try:
            self.manager.update_video_cut_mode(video_id, cut_mode)
            self.manager.update_video_status(video_id, "rendering", note=video.note)
            render_config = replace(self.config, video_cut_mode=cut_mode)
            orchestrator = SessionOrchestrator(config=render_config, logger=self.logger)
            session_result = orchestrator.run(
                SessionSpec(
                    items=[
                        SessionItemSpec(
                            row_id="profile_video_%s" % video_id,
                            source_video_url=source_video_url,
                            product_image=product_image_path,
                        )
                    ],
                    output_root_dir=render_config.default_output_root,
                    session_name="profile_video_%s" % video_id,
                    cookies_file=None,
                )
            )
            if not session_result.items:
                raise RuntimeError("Pipeline did not return a rendered item.")
            item_result = session_result.items[0]
            final_path = item_result.artifacts.final_video_path
            if item_result.status != "completed" or final_path is None or not final_path.exists():
                raise RuntimeError(item_result.error or "Pipeline did not create a final video.")
            profile_slug = self._video_profile_slug(video)
            stored_final_path = copy_rendered_video_to_queue(profile_slug, final_path)
            updated = self.manager.mark_video_rendered(
                video_id,
                stored_final_path,
                source_title=str(item_result.metadata.get("source_title") or "").strip() or None,
            )
            self.manager.add_log(
                "info",
                "video_render_completed",
                "Rendered video %s with cut mode %s." % (video_id, cut_mode),
                account_id=updated.account_id,
                video_id=video_id,
            )
            return {
                "video_id": video_id,
                "file_path": updated.file_path,
                "cut_mode": cut_mode,
            }
        except Exception as exc:
            try:
                self.manager.update_video_status(video_id, "error", note=str(exc))
                self.manager.add_log("error", "video_render_error", "Video %s render failed: %s" % (video_id, exc), account_id=video.account_id, video_id=video_id)
            except Exception:
                pass
            raise

    def _handle_video_render_success(self, result: dict) -> None:
        video_id = int(result.get("video_id") or 0)
        self.video_render_pending_ids.discard(video_id)
        self._refresh_videos()
        self._refresh_logs()
        if video_id and self.video_table.exists(str(video_id)):
            self.video_table.selection_set(str(video_id))
        self.status_var.set("Đã tạo xong video %s. Action đã chuyển thành Play." % video_id)

    def _handle_video_render_error(self, payload: dict) -> None:
        video_id = int(payload.get("video_id") or 0)
        error = payload.get("error")
        self.video_render_pending_ids.discard(video_id)
        self._refresh_videos()
        self._refresh_logs()
        self.status_var.set("Tạo video %s lỗi: %s" % (video_id, error))
        messagebox.showerror("Create video", "Video %s: %s" % (video_id, error))

    def _resolve_project_path(self, value: str) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        path = Path(text)
        if path.is_absolute():
            return path
        return (Path(self.manager.project_root).resolve() / path).resolve()

    def _video_profile_slug(self, video) -> str:
        if video.account_id is not None:
            account = self.manager.get_account(video.account_id)
            if account is not None:
                return slugify(account.name)
        return "unassigned"

    def _open_video_product_link(self, row_id: str) -> None:
        try:
            video_id = int(row_id)
        except (TypeError, ValueError):
            return
        video = self.manager.get_video(video_id)
        if video is None:
            return
        product_id = (video.product_id or "").strip()
        if not product_id:
            self.status_var.set("Video này chưa có Product ID.")
            return
        product_url = self._product_url_for_video(video)
        try:
            webbrowser.open(product_url)
            self.status_var.set("Opened product link for Product ID %s." % product_id)
        except Exception as exc:
            messagebox.showerror("Open product link failed", "Could not open product link: %s" % exc)

    def _open_product_url(self, product_url: str, product_id: str = "") -> None:
        try:
            webbrowser.open(product_url)
            if product_id:
                self.status_var.set("Opened product link for Product ID %s." % product_id)
            else:
                self.status_var.set("Opened product link.")
        except Exception as exc:
            messagebox.showerror("Open product link failed", "Could not open product link: %s" % exc)

    def _on_video_table_motion(self, event) -> None:
        if self._pointer_inside_product_link_tooltip(event.x_root, event.y_root):
            self._cancel_hide_product_link_tooltip()
            return
        if self.product_link_tooltip is not None and self._pointer_between_product_cell_and_tooltip(event.x_root, event.y_root):
            self._cancel_hide_product_link_tooltip()
            return
        row_id, column = self.video_table.identify_cell(event.x, event.y)
        cursor_active = False
        if column in ("action", "cut_mode") and row_id:
            cursor_active = True
        elif column == "play" and row_id:
            try:
                video = self.manager.get_video(int(row_id)) if str(row_id).isdigit() else None
                if video and self._video_final_path_exists(video):
                    cursor_active = True
            except Exception:
                pass
        self.video_table.tree.configure(
            cursor="hand2" if cursor_active else "",
        )
        if cursor_active and column in ("action", "play", "cut_mode"):
            self._set_video_play_hover(row_id)
        else:
            self._clear_video_play_hover()
        if column != "product_id" or not row_id:
            self._schedule_hide_product_link_tooltip()
            return
        video = self.manager.get_video(int(row_id)) if str(row_id).isdigit() else None
        if video is None or not (video.product_id or "").strip():
            self._schedule_hide_product_link_tooltip()
            return
        box = self.video_table.tree.bbox(row_id, "product_id")
        if not box:
            self._schedule_hide_product_link_tooltip()
            return
        tree_x = self.video_table.tree.winfo_rootx()
        tree_y = self.video_table.tree.winfo_rooty()
        x_root = tree_x + box[0]
        y_root = tree_y + box[1]
        self._show_product_link_tooltip(
            self._product_url_for_video(video),
            video.product_id,
            row_id,
            x_root,
            y_root,
            box[2],
            box[3],
        )

    def _on_video_table_leave(self, _event=None) -> None:
        self.video_table.tree.configure(cursor="")
        self._clear_video_play_hover()
        self._schedule_hide_product_link_tooltip(delay_ms=500)

    def _set_video_play_hover(self, row_id: str) -> None:
        if self.video_play_hover_row_id == str(row_id):
            return
        self._clear_video_play_hover()
        if not self.video_table.exists(str(row_id)):
            return
        values = list(self.video_table.item(row_id, "values"))
        try:
            play_index = self.video_table.columns.index("play")
        except ValueError:
            return
        if play_index < len(values):
            self.video_table.item(row_id, values=values, tags=("play_hover",))
        self.video_play_hover_row_id = str(row_id)
        try:
            self.video_table.tree.configure(cursor="hand2")
        except tk.TclError:
            pass

    def _clear_video_play_hover(self) -> None:
        row_id = self.video_play_hover_row_id
        self.video_play_hover_row_id = ""
        if row_id and self.video_table.exists(row_id):
            video = self.manager.get_video(int(row_id)) if str(row_id).isdigit() else None
            tags = (self._status_tag(video.status),) if video is not None else ()
            self.video_table.item(row_id, tags=tags)
        try:
            self.video_table.tree.configure(cursor="")
        except tk.TclError:
            pass

    def _show_product_link_tooltip(
        self,
        product_url: str,
        product_id: str,
        row_id: str,
        x_root: int,
        y_root: int,
        cell_width: int,
        cell_height: int,
    ) -> None:
        self._cancel_hide_product_link_tooltip()
        if (
            self.product_link_tooltip is not None
            and self.product_link_tooltip_url == product_url
            and self.product_link_tooltip_row_id == str(row_id)
        ):
            try:
                self.product_link_tooltip.geometry("%sx%s+%s+%s" % (max(180, cell_width), max(28, cell_height), x_root, y_root))
            except tk.TclError:
                pass
            return
        self._hide_product_link_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.withdraw()
        tooltip.overrideredirect(True)
        tooltip.configure(bg=COLORS["surface_2"])
        label = tk.Label(
            tooltip,
            text="/view/product/%s" % product_id,
            bg=COLORS["surface_2"],
            fg=COLORS["accent_2"],
            padx=10,
            pady=0,
            cursor="hand2",
            width=1,
            justify="left",
            font=(FONT, 10, "underline"),
            anchor="w",
        )
        label.pack(fill="both", expand=True)
        label.bind("<Button-1>", lambda _event, url=product_url, pid=product_id: self._open_product_url(url, pid))
        tooltip.bind("<Enter>", lambda _event: self._cancel_hide_product_link_tooltip())
        tooltip.bind("<Leave>", lambda _event: self._schedule_hide_product_link_tooltip(delay_ms=300))
        label.bind("<Enter>", lambda _event: self._cancel_hide_product_link_tooltip())
        label.bind("<Leave>", lambda _event: self._schedule_hide_product_link_tooltip(delay_ms=300))
        tooltip.update_idletasks()
        tooltip.geometry("%sx%s+%s+%s" % (max(180, cell_width), max(28, cell_height), x_root, y_root))
        tooltip.deiconify()
        self.product_link_tooltip = tooltip
        self.product_link_tooltip_url = product_url
        self.product_link_tooltip_row_id = str(row_id)

    def _schedule_hide_product_link_tooltip(self, delay_ms: int = 250) -> None:
        self._cancel_hide_product_link_tooltip()
        self.product_link_tooltip_after = self.after(delay_ms, self._hide_product_link_tooltip)

    def _cancel_hide_product_link_tooltip(self) -> None:
        if self.product_link_tooltip_after is None:
            return
        try:
            self.after_cancel(self.product_link_tooltip_after)
        except tk.TclError:
            pass
        self.product_link_tooltip_after = None

    def _hide_product_link_tooltip(self) -> None:
        self._cancel_hide_product_link_tooltip()
        tooltip = self.product_link_tooltip
        self.product_link_tooltip = None
        self.product_link_tooltip_url = ""
        self.product_link_tooltip_row_id = ""
        if tooltip is None:
            return
        try:
            tooltip.destroy()
        except tk.TclError:
            pass

    def _pointer_inside_product_link_tooltip(self, x_root: int, y_root: int) -> bool:
        tooltip = self.product_link_tooltip
        if tooltip is None:
            return False
        try:
            left = tooltip.winfo_rootx()
            top = tooltip.winfo_rooty()
            right = left + tooltip.winfo_width()
            bottom = top + tooltip.winfo_height()
        except tk.TclError:
            return False
        margin = 4
        return (left - margin) <= x_root <= (right + margin) and (top - margin) <= y_root <= (bottom + margin)

    def _pointer_between_product_cell_and_tooltip(self, x_root: int, y_root: int) -> bool:
        tooltip = self.product_link_tooltip
        row_id = self.product_link_tooltip_row_id
        if tooltip is None or not row_id:
            return False
        try:
            box = self.video_table.tree.bbox(row_id, "product_id")
            if not box:
                return False
            cell_left = self.video_table.tree.winfo_rootx() + box[0]
            cell_top = self.video_table.tree.winfo_rooty() + box[1]
            cell_right = cell_left + box[2]
            cell_bottom = cell_top + box[3]
            tooltip_left = tooltip.winfo_rootx()
            tooltip_top = tooltip.winfo_rooty()
            tooltip_right = tooltip_left + tooltip.winfo_width()
            tooltip_bottom = tooltip_top + tooltip.winfo_height()
        except tk.TclError:
            return False
        left = min(cell_left, tooltip_left)
        right = max(cell_right, tooltip_right)
        top = min(cell_top, tooltip_top)
        bottom = max(cell_bottom, tooltip_bottom)
        return left <= x_root <= right and top <= y_root <= bottom

    def _product_url_for_video(self, video) -> str:
        product_id = (video.product_id or "").strip()
        note_url = _extract_product_url_from_note(video.note)
        if note_url:
            return note_url
        return "https://www.tiktok.com/view/product/%s" % product_id

    def _delete_current_video(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        message = "Delete selected video?\n\nThis will remove the file from this computer."
        if not messagebox.askyesno("Delete video", message):
            return
        try:
            report = self.manager.delete_videos([video.id])
        except Exception as exc:
            messagebox.showerror("Delete video failed", str(exc))
            return
        for video_id in report["deleted_ids"]:
            self.manager.add_log("info", "video_delete", "Deleted video %s from disk and database." % video_id, video_id=video_id)
        self._refresh_videos()
        self._refresh_logs()
        deleted = report["deleted"]
        missing = report["missing_files"]
        errors = report["errors"]
        if errors:
            messagebox.showerror("Delete video failed", "\n".join(errors))
        status = "Deleted %s video(s) from disk and database." % deleted
        if missing:
            status += " %s file(s) were already missing." % missing
        self.status_var.set(status)

    def _on_video_detail_field_changed(self, *_args) -> None:
        if not self.video_detail_loading:
            self._ensure_video_detail_account_hashtag()
        self._schedule_video_detail_autosave()

    def _on_video_detail_limited_text_modified(self, textbox) -> None:
        self._limit_textbox_lines(textbox, max_lines=3)
        self._schedule_video_detail_autosave()

    def _on_video_detail_text_modified(self, _event=None) -> None:
        self._schedule_video_detail_autosave()

    def _copy_video_detail_caption_with_hashtags(self) -> None:
        text = _compose_video_caption_with_hashtags(
            self.video_detail_caption_text.get("1.0", "end-1c") if hasattr(self, "video_detail_caption_text") else "",
            self._video_detail_hashtags_text(),
        )
        if not text:
            self.status_var.set("Nothing to copy.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
        except tk.TclError as exc:
            messagebox.showerror("Copy caption", str(exc))
            return
        self.status_var.set("Copied description and hashtags.")

    def _send_video_detail_product_id_to_phone_clipboard(self) -> None:
        if not self._flush_video_detail_autosave(show_errors=True):
            return
        video = self._selected_video()
        if video is None:
            return
        product_id = str(self.video_detail_product_var.get() or video.product_id or "").strip()
        if not product_id:
            messagebox.showinfo("Missing Product ID", "Video chua co Product ID de gui sang clipboard dien thoai.")
            return
        if not product_id.isdigit():
            messagebox.showerror("Invalid Product ID", "Product ID chi duoc chua chu so: %s" % product_id)
            return
        address = self._phone_video_target_address()
        if address is None:
            return

        def worker() -> dict:
            if not self.phone_controller.is_connected():
                self.phone_controller.connect(address)
            return self.phone_controller.copy_text_to_clipboard(
                product_id,
                label="Product ID",
                address=address,
                sync_to_phone=True,
                require_phone_clipboard=True,
            )

        def on_success(result: dict) -> None:
            message = str(result.get("message") or "Product ID copied to phone clipboard.")
            self.status_var.set(message)
            self.manager.add_log(
                "info",
                "phone_product_id_clipboard",
                message,
                account_id=video.account_id,
                video_id=video.id,
            )
            self._refresh_logs()
            self._show_success_notification("Đã copy Product ID vào clipboard điện thoại.")

        self._run_worker(
            "Copying Product ID to phone clipboard...",
            worker,
            on_success=on_success,
            error_title="Product ID clipboard",
        )

    def _set_video_detail_hashtags(self, value: str) -> None:
        if hasattr(self, "video_detail_hashtags_input"):
            self.video_detail_hashtags_input.set_hashtags(value)

    def _video_detail_hashtags_text(self) -> str:
        if not hasattr(self, "video_detail_hashtags_input"):
            return ""
        return self.video_detail_hashtags_input.get_hashtags()

    def _ensure_video_detail_account_hashtag(self) -> bool:
        if not hasattr(self, "video_detail_hashtags_input"):
            return False
        account_label = self.video_detail_account_var.get().strip()
        account_id = getattr(self, "video_account_ids_by_label", {}).get(account_label)
        account = self.manager.get_account(account_id) if account_id is not None else None
        hashtags = getattr(account, "hashtags", "") if account is not None else ""
        if not hashtags:
            return False
        changed = False
        for hashtag in _hashtag_tokens_for_ui(hashtags):
            changed = self.video_detail_hashtags_input.add_hashtag(hashtag, notify=False) or changed
        return changed

    def _limit_textbox_lines(self, textbox, max_lines: int) -> None:
        text = textbox.get("1.0", "end-1c")
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return
        trimmed = "\n".join(lines[:max_lines])
        textbox.delete("1.0", tk.END)
        textbox.insert("1.0", trimmed)

    def _schedule_video_detail_autosave(self) -> None:
        if self.video_detail_loading or self.closing or self.video_detail_video_id is None:
            return
        self.video_detail_dirty = True
        if self.video_detail_autosave_after is not None:
            try:
                self.after_cancel(self.video_detail_autosave_after)
            except tk.TclError:
                pass
        self.video_detail_autosave_after = self.after(650, self._autosave_video_detail)

    def _autosave_video_detail(self) -> None:
        self.video_detail_autosave_after = None
        self._save_video_detail(show_errors=True)

    def _flush_video_detail_autosave(self, show_errors: bool = True) -> bool:
        if self.video_detail_autosave_after is not None:
            try:
                self.after_cancel(self.video_detail_autosave_after)
            except tk.TclError:
                pass
            self.video_detail_autosave_after = None
        if not self.video_detail_dirty:
            return True
        return self._save_video_detail(show_errors=show_errors)

    def _current_selected_video(self):
        video_id = self._selected_video_id()
        if video_id is None:
            return None
        return self.manager.get_video(video_id)

    def _on_video_file_label_enter(self, _event=None) -> None:
        if not getattr(self, "video_detail_file_var", None) or not self.video_detail_file_var.get().strip():
            return
        self.video_detail_file_label.configure(text_color=COLORS["accent_2"], cursor="hand2")

    def _on_video_file_label_leave(self, _event=None) -> None:
        self.video_detail_file_label.configure(text_color=COLORS["text"], cursor="")

    def _open_selected_video_folder(self, _event=None) -> None:
        video = self._current_selected_video()
        if video is None:
            return
        try:
            video_path = self.manager.resolve_video_path(video)
            folder = video_path.parent
            if not folder.exists() or not folder.is_dir():
                raise ValueError("Video folder does not exist: %s" % folder)
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,%s" % str(video_path)])
            else:
                opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
                subprocess.Popen([opener, str(folder)])
        except Exception as exc:
            messagebox.showerror("Open video folder", str(exc))

    def _load_video_detail(self, video) -> None:
        if not hasattr(self, "video_detail_id_var"):
            return
        if self.video_detail_dirty:
            return
        self.video_detail_loading = True
        if video is None:
            try:
                self.video_detail_video_id = None
                self.video_detail_id_var.set("-")
                self.video_detail_profile_var.set("")
                self.video_detail_file_var.set("")
                self.video_detail_source_var.set("")
                self.video_detail_status_var.set("")
                self.video_detail_updated_var.set("")
                self.video_detail_account_var.set("")
                self.video_detail_product_var.set("")
                self.video_detail_publish_mode_var.set("now")
                self.video_detail_scheduled_var.set("")
                self.video_detail_caption_text.delete("1.0", tk.END)
                self._set_video_detail_hashtags("")
            finally:
                self.video_detail_loading = False
            return
        account_names = {account.id: account.name for account in self.manager.list_accounts()}
        account_label = ""
        if video.account_id is not None:
            account_label = "%s - %s" % (video.account_id, account_names.get(video.account_id, ""))
        elif getattr(self, "video_account_ids_by_label", None):
            account_label = next(iter(self.video_account_ids_by_label))
        try:
            self.video_detail_video_id = video.id
            self.video_detail_id_var.set(self._video_display_number(video.id))
            self.video_detail_profile_var.set(account_names.get(video.account_id, "") if video.account_id is not None else "")
            self.video_detail_file_var.set(video.file_path if self._video_final_path_exists(video) else "")
            self.video_detail_source_var.set(video.source)
            self.video_detail_status_var.set(video.status)
            self.video_detail_updated_var.set(_format_vietnam_datetime(video.updated_at))
            self.video_detail_account_var.set(account_label)
            self.video_detail_product_var.set(video.product_id)
            self.video_detail_publish_mode_var.set(video.publish_mode)
            self.video_detail_scheduled_var.set(_format_vietnam_datetime(video.scheduled_at, assume_utc=False))
            self.video_detail_caption_text.delete("1.0", tk.END)
            self.video_detail_caption_text.insert("1.0", video.caption)
            self._set_video_detail_hashtags(video.hashtags)
            added_account_hashtag = self._ensure_video_detail_account_hashtag()
            self.video_detail_dirty = added_account_hashtag
        finally:
            self.video_detail_loading = False
        if video is not None and self.video_detail_dirty:
            self._schedule_video_detail_autosave()

    def _video_display_number(self, video_id: int) -> str:
        if not hasattr(self, "video_table") or not self.video_table.exists(str(video_id)):
            return "-"
        values = self.video_table.item(str(video_id), "values")
        try:
            row_index = self.video_table.columns.index("row_no")
            return str(values[row_index])
        except (ValueError, IndexError):
            return "-"

    def _refresh_logs(self) -> None:
        logs = self.manager.list_logs()
        self.log_count_var.set(str(len(logs)))
        self.log_table.delete(*self.log_table.get_children())
        for log in logs:
            self.log_table.insert(
                "",
                tk.END,
                iid=str(log.id),
                tags=(self._status_tag(log.level),),
                values=(
                    log.id,
                    _format_vietnam_datetime(log.created_at),
                    log.level,
                    log.action,
                    log.account_id or "",
                    log.video_id or "",
                    log.message,
                ),
            )
        self.log_snapshot = self._log_snapshot(logs)

    def _clear_logs(self) -> None:
        if not self.manager.list_logs():
            self.status_var.set("No logs to clear.")
            return
        if not messagebox.askyesno("Clear logs", "Delete all activity logs?"):
            return
        deleted = self.manager.clear_logs()
        self._refresh_logs()
        self.status_var.set("Deleted %s log(s)." % deleted)

    def _cleanup_tool_storage(self) -> None:
        if self.telegram_bot_process is not None and self.telegram_bot_process.poll() is None:
            messagebox.showinfo("Cleanup", "Stop the Telegram bot before cleaning tool data.")
            return
        message = (
            "Cleanup will delete generated tool data: output/input videos, profile video queue, "
            "temp files, phone screenshots, and log files.\n\n"
            "Accounts, sources, config files, and browser profile/login folders will be kept. "
            "Video rows whose files are deleted will be removed from the table. Continue?"
        )
        if not messagebox.askyesno("Cleanup tool data", message):
            return

        def worker():
            self.browser.close_all()
            return cleanup_tool_storage(self.config, self.manager.project_root)

        def on_success(report):
            missing_video_ids = [
                video.id
                for video in self.manager.list_videos()
                if not self.manager.resolve_video_path(video).exists()
            ]
            video_delete_report = (
                self.manager.delete_videos(missing_video_ids)
                if missing_video_ids
                else {"deleted": 0, "errors": []}
            )
            message_text = format_tool_cleanup_report(report)
            deleted_videos = int(video_delete_report.get("deleted") or 0)
            if deleted_videos:
                message_text += " Removed %s stale video row(s)." % deleted_videos
            self.manager.add_log("info", "tool_cleanup", message_text)
            self._refresh_all()
            self.status_var.set(message_text)
            cleanup_errors = list(report.errors) + list(video_delete_report.get("errors") or [])
            if cleanup_errors:
                messagebox.showwarning("Cleanup finished", "%s\n\nSome items could not be deleted because they may be in use." % message_text)
            else:
                messagebox.showinfo("Cleanup finished", message_text)

        self._run_worker(
            "Cleaning tool data...",
            worker,
            on_success=on_success,
            error_title="Cleanup",
        )

    def _status_tag(self, value: str) -> str:
        if value in ("live", "posted", "scheduled", "prepared", "info"):
            return "live"
        if value in ("error", "product_error", "selector_error"):
            return "error"
        if value in ("need_login", "checkpoint", "no_shop", "warning", "queued", "rendering"):
            return "warning"
        return "muted"

    def _refresh_all(self) -> None:
        self._refresh_accounts()
        self._refresh_sources()
        self._refresh_videos()
        self._refresh_logs()

    def _add_account(self) -> None:
        dialog = AccountDialog(self)
        if dialog.result is None:
            return
        try:
            account = self.manager.add_account(
                name=dialog.result["name"],
                login_type=dialog.result["login_type"],
                note=dialog.result["note"],
                bot_name=dialog.result["bot_name"],
                cut_mode=dialog.result["cut_mode"],
                hashtags=dialog.result["hashtags"],
            )
        except Exception as exc:
            messagebox.showerror("Add account failed", str(exc))
            return
        self._refresh_accounts()
        self.account_table.selection_set(str(account.id))
        self.status_var.set("Created profile: %s" % account.profile_path)

    def _add_video(self) -> None:
        file_path = filedialog.askopenfilename(title="Select video", filetypes=(("Video files", "*.mp4 *.mov *.m4v *.avi *.mkv"), ("All files", "*.*")))
        if not file_path:
            return
        dialog = VideoDialog(self, file_path)
        if dialog.result is None:
            return
        try:
            account_id = self._selected_account_id()
            video = self.manager.add_video(
                file_path=dialog.result["file_path"],
                caption=dialog.result["caption"],
                hashtags=dialog.result["hashtags"],
                note=dialog.result["note"],
                account_id=account_id,
                product_id=dialog.result["product_id"],
                publish_mode=dialog.result["publish_mode"],
                scheduled_at=dialog.result["scheduled_at"],
            )
            self.manager.add_log("info", "video_add", "Added video %s" % video.file_path, video_id=video.id)
        except Exception as exc:
            messagebox.showerror("Add video failed", str(exc))
            return
        self._refresh_videos()
        self._refresh_logs()
        self.video_table.selection_set(str(video.id))
        self.status_var.set("Added video: %s" % video.file_path)

    def _phone_video_target_address(self, require_connected: bool = False) -> str | None:
        address = self._sync_phone_address_from_parts()
        if not address:
            messagebox.showinfo("Phone address missing", "Enter the phone IP address before sending video.")
            return None
        if require_connected and not self.phone_controller.is_connected():
            messagebox.showinfo("Phone not connected", "Connect the phone first, then try again.")
            return None
        return address

    def _phone_video_transfer_item(self, video) -> dict:
        video_path = self.manager.resolve_video_path(video)
        if not self._video_final_path_exists(video):
            raise ValueError("Video %s chưa có final video. Bấm Tạo trước." % video.id)
        return {
            "video_id": video.id,
            "account_id": video.account_id,
            "file_path": video_path,
        }

    def _send_selected_video_to_phone(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        address = self._phone_video_target_address()
        if address is None:
            return
        try:
            phone_item = self._phone_video_transfer_item(video)
        except Exception as exc:
            messagebox.showwarning("Send to phone", str(exc))
            return
        caption_message = _compose_video_caption_with_hashtags(video.caption, video.hashtags)
        if not caption_message:
            messagebox.showinfo("Missing Description", "Video chua co mo ta/hashtag de gui sang clipboard dien thoai.")
            return
        product_id = str(video.product_id or "").strip()
        if not product_id:
            messagebox.showinfo("Missing Product ID", "Video chua co Product ID de gui sang clipboard dien thoai.")
            return
        if not product_id.isdigit():
            messagebox.showerror("Invalid Product ID", "Product ID chi duoc chua chu so: %s" % product_id)
            return

        def worker():
            if not self.phone_controller.is_connected():
                self.phone_controller.connect(address)
            phone_result = self.phone_controller.send_file_to_gallery(address, phone_item["file_path"])
            caption_clipboard_result = self.phone_controller.copy_text_to_clipboard(
                caption_message,
                label="Description and hashtags",
                address=address,
                sync_to_phone=True,
                require_phone_clipboard=True,
            )
            product_id_clipboard_result = self.phone_controller.copy_text_to_clipboard(
                product_id,
                label="Product ID",
                address=address,
                sync_to_phone=True,
                require_phone_clipboard=True,
            )
            return {
                "video_id": video.id,
                "account_id": video.account_id,
                "phone": phone_result,
                "caption_clipboard_result": caption_clipboard_result,
                "product_id_clipboard_result": product_id_clipboard_result,
            }

        def on_success(payload):
            result = payload["phone"]
            self.manager.add_log(
                "info",
                "phone_video_send",
                "Sent video to phone and copied description/hashtags plus Product ID to phone clipboard: %s"
                % result["remote_path"],
                account_id=payload["account_id"],
                video_id=payload["video_id"],
            )
            self._refresh_logs()
            self.status_var.set(
                "Sent video %s to phone. Copied description/hashtags and Product ID as 2 separate clipboard items. Current clipboard item: Product ID."
                % payload["video_id"]
            )
            self._show_success_notification("Đã gửi video và copy mô tả/hashtag + Product ID vào clipboard điện thoại.")

        self._run_worker(
            "Sending video %s to phone and copying product messages..." % video.id,
            worker,
            on_success=on_success,
            error_title="Send",
        )

    def _send_selected_video_to_telegram(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        try:
            payload = self._telegram_product_payload_for_video(video)
        except Exception as exc:
            messagebox.showerror("Send Telegram", str(exc))
            return

        def worker():
            client = TelegramBotClient(payload["bot_token"], logger=self.logger)
            self._send_telegram_product_payload(client, payload)
            return {
                "video_id": payload["video_id"],
                "account_id": payload["account_id"],
                "profile": payload["profile"],
                "chat_id": payload["chat_id"],
                "product_id": payload["product_id"],
            }

        def on_success(result):
            self._log_telegram_product_send(result)
            self._refresh_logs()
            self.status_var.set("Đã gửi sản phẩm của video %s qua Telegram." % result["video_id"])

        self._run_worker("Đang gửi sản phẩm của video %s qua Telegram..." % video.id, worker, on_success=on_success)

    def _telegram_product_payload_for_video(self, video) -> dict:
        if video.account_id is None:
            raise ValueError("Video %s chưa được gán profile." % video.id)
        account = self.manager.get_account(video.account_id)
        if account is None:
            raise ValueError("Profile của video %s không còn tồn tại." % video.id)
        bot_token, chat_id = self._telegram_target_for_account(account)
        caption_message, product_id = _telegram_product_messages_for_video(video)
        return {
            "video_id": video.id,
            "account_id": account.id,
            "profile": account.name,
            "bot_token": bot_token,
            "chat_id": chat_id,
            "caption_message": caption_message,
            "product_id": product_id,
        }

    def _send_telegram_product_payload(self, client: TelegramBotClient, payload: dict) -> None:
        chat_id = payload["chat_id"]
        client.send_message(chat_id, payload["caption_message"])
        if payload["product_id"]:
            client.send_message(chat_id, payload["product_id"])

    def _log_telegram_product_send(self, payload: dict) -> None:
        self.manager.add_log(
            "info",
            "telegram_send_product",
            "Sent product text for video %s to Telegram chat %s for profile %s%s." % (
                payload["video_id"],
                payload["chat_id"],
                payload["profile"],
                " with product ID %s" % payload["product_id"] if payload["product_id"] else "",
            ),
            account_id=payload["account_id"],
            video_id=payload["video_id"],
        )

    def _telegram_target_for_account(self, account) -> tuple[str, int]:
        payload = self._load_telegram_bots_payload()
        return _telegram_bot_config_for_account(payload, account)

    def _on_account_selected(self, _event=None) -> None:
        self._update_telegram_target_profile_label()

    def _on_account_cell_clicked(self, row_id: str, column: str) -> None:
        if column == "cut_mode":
            self._show_account_cut_mode_menu(row_id)

    def _on_account_cell_double_clicked(self, event) -> str | None:
        row_id, column = self.account_table.identify_cell(event.x, event.y)
        if not row_id:
            return None
        if column == "hashtags":
            self._begin_account_hashtags_edit(row_id)
            return "break"
        if column == "bot_name":
            self._begin_account_bot_name_edit(row_id)
            return "break"
        return None

    def _account_cut_mode_label(self, account, include_arrow: bool = True) -> str:
        cut_mode = str(getattr(account, "cut_mode", "") or "original").strip().lower()
        label = VIDEO_ROW_CUT_MODE_LABELS.get(cut_mode, VIDEO_ROW_CUT_MODE_LABELS["original"])
        return "%s ▾" % label if include_arrow else label

    def _show_account_cut_mode_menu(self, row_id: str) -> None:
        try:
            account_id = int(row_id)
        except (TypeError, ValueError):
            return
        account = self.manager.get_account(account_id)
        if account is None:
            return
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=COLORS["surface_2"],
            fg=COLORS["text"],
            activebackground=COLORS["surface_3"],
            activeforeground=COLORS["text"],
            borderwidth=2,
            activeborderwidth=2,
            font=(FONT, 13),
        )
        current = str(getattr(account, "cut_mode", "") or "original").strip().lower()
        for label, value in VIDEO_ROW_CUT_MODE_VALUES.items():
            prefix = "✓  " if value == current else "   "
            menu.add_command(
                label="  %s%s        " % (prefix, label),
                command=lambda mode=value: self._set_account_cut_mode(account_id, mode),
            )
        try:
            box = self.account_table.tree.bbox(str(row_id), "cut_mode")
            x_root = self.account_table.tree.winfo_rootx() + (box[0] if box else 0)
            y_root = self.account_table.tree.winfo_rooty() + (box[1] if box else 0) + (box[3] if box else 0)
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _set_account_cut_mode(self, account_id: int, cut_mode: str) -> None:
        try:
            updated = self.manager.update_account_cut_mode(account_id, cut_mode)
            self.manager.add_log("info", "account_cut_mode", "Account %s cut mode set to %s." % (account_id, updated.cut_mode), account_id=account_id)
        except Exception as exc:
            messagebox.showerror("Account Cut Mode", str(exc))
            return
        self._refresh_accounts()
        self._refresh_logs()
        if self.account_table.exists(str(account_id)):
            self.account_table.selection_set(str(account_id))
        self.status_var.set("Account %s Cut Mode: %s." % (updated.name, self._account_cut_mode_label(updated, include_arrow=False)))

    def _video_row_cut_mode_label(self, video, include_arrow: bool = True) -> str:
        cut_mode = str(getattr(video, "cut_mode", "") or "original").strip().lower()
        label = VIDEO_ROW_CUT_MODE_LABELS.get(cut_mode, VIDEO_ROW_CUT_MODE_LABELS["original"])
        return "%s ▾" % label if include_arrow else label

    def _show_video_cut_mode_menu(self, row_id: str) -> None:
        try:
            video_id = int(row_id)
        except (TypeError, ValueError):
            return
        video = self.manager.get_video(video_id)
        if video is None:
            return
        menu = tk.Menu(
            self,
            tearoff=False,
            bg=COLORS["surface_2"],
            fg=COLORS["text"],
            activebackground=COLORS["surface_3"],
            activeforeground=COLORS["text"],
            borderwidth=2,
            activeborderwidth=2,
            font=(FONT, 13),
        )
        current = str(getattr(video, "cut_mode", "") or "original").strip().lower()
        for label, value in VIDEO_ROW_CUT_MODE_VALUES.items():
            prefix = "✓  " if value == current else "   "
            menu.add_command(
                label="  %s%s        " % (prefix, label),
                command=lambda mode=value: self._set_video_cut_mode(video_id, mode),
            )
        try:
            box = self.video_table.tree.bbox(str(row_id), "cut_mode")
            x = self.video_table.tree.winfo_rootx() + (box[0] if box else 0)
            y = self.video_table.tree.winfo_rooty() + (box[1] if box else 0) + (box[3] if box else 0)
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_video_cut_mode(self, video_id: int, cut_mode: str) -> None:
        try:
            updated = self.manager.update_video_cut_mode(video_id, cut_mode)
            self.manager.add_log("info", "video_cut_mode", "Video %s cut mode set to %s." % (video_id, updated.cut_mode), video_id=video_id)
        except Exception as exc:
            messagebox.showerror("Video Cut Mode", str(exc))
            return
        self._refresh_videos()
        self._refresh_logs()
        if self.video_table.exists(str(video_id)):
            self.video_table.selection_set(str(video_id))
        self.status_var.set("Video %s Cut Mode: %s." % (updated.id, self._video_row_cut_mode_label(updated, include_arrow=False)))

    def _begin_account_hashtags_edit(self, row_id: str) -> None:
        try:
            account_id = int(row_id)
        except (TypeError, ValueError):
            return
        account = self.manager.get_account(account_id)
        if account is None:
            return
        self._cancel_account_hashtags_edit()
        box = self.account_table.tree.bbox(str(row_id), "hashtags")
        if not box:
            return
        x, y, width, height = box
        variable = tk.StringVar(value=getattr(account, "hashtags", "") or "")
        editor = tk.Entry(
            self.account_table.tree,
            textvariable=variable,
            bg=COLORS["input"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="solid",
            bd=1,
            font=(FONT, 14),
        )
        editor.place(x=x, y=y, width=width, height=height)
        editor.select_range(0, tk.END)
        editor.focus_set()
        self.account_hashtags_editor = editor
        self.account_hashtags_editor_info = {
            "account_id": account_id,
            "field": "hashtags",
            "variable": variable,
        }
        editor.bind("<Return>", lambda _event: self._commit_account_hashtags_edit())
        editor.bind("<FocusOut>", lambda _event: self._commit_account_hashtags_edit())
        editor.bind("<Escape>", lambda _event: self._cancel_account_hashtags_edit())

    def _begin_account_bot_name_edit(self, row_id: str) -> None:
        try:
            account_id = int(row_id)
        except (TypeError, ValueError):
            return
        account = self.manager.get_account(account_id)
        if account is None:
            return
        self._cancel_account_hashtags_edit()
        box = self.account_table.tree.bbox(str(row_id), "bot_name")
        if not box:
            return
        x, y, width, height = box
        variable = tk.StringVar(value=getattr(account, "bot_name", "") or "")
        editor = tk.Entry(
            self.account_table.tree,
            textvariable=variable,
            bg=COLORS["input"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="solid",
            bd=1,
            font=(FONT, 14),
        )
        editor.place(x=x, y=y, width=width, height=height)
        editor.select_range(0, tk.END)
        editor.focus_set()
        self.account_hashtags_editor = editor
        self.account_hashtags_editor_info = {
            "account_id": account_id,
            "field": "bot_name",
            "variable": variable,
        }
        editor.bind("<Return>", lambda _event: self._commit_account_hashtags_edit())
        editor.bind("<FocusOut>", lambda _event: self._commit_account_hashtags_edit())
        editor.bind("<Escape>", lambda _event: self._cancel_account_hashtags_edit())

    def _commit_account_hashtags_edit(self, show_errors: bool = True) -> str:
        editor = getattr(self, "account_hashtags_editor", None)
        info = getattr(self, "account_hashtags_editor_info", None)
        if editor is None or not info:
            return "break"
        self.account_hashtags_editor = None
        self.account_hashtags_editor_info = None
        try:
            value = info["variable"].get()
        except Exception:
            value = ""
        try:
            editor.destroy()
        except tk.TclError:
            pass
        try:
            account_id = int(info["account_id"])
            field = str(info.get("field") or "hashtags")
            account = self.manager.get_account(account_id)
            if field == "bot_name":
                normalized = str(value or "").strip()
                if account is not None and (account.bot_name or "").strip() == normalized:
                    return "break"
                updated = self.manager.update_account_bot_name(account_id, normalized)
                self.manager.add_log(
                    "info",
                    "account_bot_name",
                    "Account %s bot_name set to %s." % (updated.id, updated.bot_name or "(none)"),
                    account_id=updated.id,
                )
            else:
                normalized = normalize_hashtags(value)
                if account is not None and (account.hashtags or "").strip() == normalized:
                    return "break"
                updated = self.manager.update_account_hashtags(account_id, normalized)
                self.manager.add_log(
                    "info",
                    "account_hashtags",
                    "Account %s hashtags set to %s." % (updated.id, updated.hashtags or "(none)"),
                    account_id=updated.id,
                )
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Account", str(exc))
            else:
                self.status_var.set("Account save failed: %s" % exc)
            return "break"
        self._refresh_accounts()
        self._refresh_logs()
        if self.account_table.exists(str(updated.id)):
            self.account_table.selection_set(str(updated.id))
        if str(info.get("field") or "hashtags") == "bot_name":
            self.status_var.set("Account %s bot_name: %s." % (updated.name, updated.bot_name or "(none)"))
        else:
            self.status_var.set("Account %s hashtags: %s." % (updated.name, updated.hashtags or "(none)"))
        return "break"

    def _cancel_account_hashtags_edit(self) -> str:
        editor = getattr(self, "account_hashtags_editor", None)
        self.account_hashtags_editor = None
        self.account_hashtags_editor_info = None
        if editor is not None:
            try:
                editor.destroy()
            except tk.TclError:
                pass
        return "break"

    def _selected_account_for_telegram(self):
        account_id = self._selected_account_id()
        if account_id is None:
            return None
        return self.manager.get_account(account_id)

    def _telegram_effective_save_to_profile(self) -> bool:
        save_to_profile = bool(self.telegram_save_profile_var.get())
        send_result = bool(self.telegram_send_result_var.get())
        if not save_to_profile and not send_result:
            return True
        return save_to_profile

    def _update_telegram_target_profile_label(self) -> None:
        if not hasattr(self, "telegram_target_profile_var"):
            return
        if not self._telegram_effective_save_to_profile():
            self.telegram_target_profile_var.set("Telegram videos will not be saved to a profile")
            return
        self.telegram_target_profile_var.set("Telegram videos will be saved by bot/profile mapping.")

    def _append_telegram_event(self, message: str) -> None:
        if not hasattr(self, "telegram_event_log"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.telegram_event_log.configure(state="normal")
        self.telegram_event_log.insert("end", "[%s] %s\n" % (timestamp, message))
        self.telegram_event_log.see("end")
        self.telegram_event_log.configure(state="disabled")

    def _format_float_setting(self, value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return ("%.3f" % number).rstrip("0").rstrip(".")

    def _queue_phone_event(self, payload: dict[str, object]) -> None:
        self.events.put(("phone_event", payload, None, None))

    def _queue_phone_screenshot_hotkey(self) -> None:
        self.events.put(("phone_screenshot_hotkey", None, None, None))

    def _queue_phone_close_hotkey(self) -> None:
        self.events.put(("phone_close_hotkey", None, None, None))

    def _handle_phone_event(self, payload: dict[str, object]) -> None:
        level = str(payload.get("level") or "info")
        action = str(payload.get("action") or "phone_event")
        message = str(payload.get("message") or action)
        self.manager.add_log(level, action, message)
        self._append_telegram_event("[%s] %s" % (action, message))

        if action == "phone_connected":
            self.phone_metric_var.set("Connected")
            self.phone_control_status_var.set(message)
        elif action == "scrcpy_started":
            self.phone_metric_var.set("Control")
        elif action == "scrcpy_closed":
            if self.phone_controller.is_connected():
                self.phone_metric_var.set("Connected")
                self.phone_control_status_var.set("Phone connected for file transfer.")
            else:
                self.phone_metric_var.set("Stopped")
                self.phone_control_status_var.set("Phone control stopped")
        elif action == "phone_transfer_started":
            self.phone_last_transfer_var.set(message)
        elif action == "phone_transfer_completed":
            self.phone_last_transfer_var.set("%s Adding to Gallery..." % message)
        elif action == "phone_gallery_ready":
            self.phone_last_transfer_var.set(message)
            self.status_var.set(message)
        elif action == "phone_transfer_failed":
            self.phone_last_transfer_var.set(message)
            self.status_var.set(message)

        if hasattr(self, "log_table"):
            self._refresh_logs()

    def _connect_phone(self) -> None:
        address = self._sync_phone_address_from_parts()
        settings = self._phone_control_settings(address)
        self.phone_control_status_var.set("Connecting to phone...")
        self.phone_metric_var.set("Connecting")

        def on_success(result: dict) -> None:
            normalized_address = str(result.get("address") or address)
            self._set_phone_address(normalized_address)
            try:
                save_phone_control_settings(self._phone_control_settings(normalized_address))
            except OSError as exc:
                self.logger.warning("Could not save phone control settings: %s", exc)
            message = str(result.get("message") or "Phone connected for file transfer.")
            self.phone_control_status_var.set(message)
            self.status_var.set(message)
            self._set_phone_control_running(self.phone_controller.is_running())

        self._run_worker(
            "Connecting phone for file transfer...",
            lambda: self.phone_controller.connect(settings.address),
            on_success=on_success,
            error_title="Phone control",
        )

    def _start_phone_control(self) -> None:
        if not self.phone_controller.is_connected():
            self.phone_control_status_var.set("Connect the phone before opening control.")
            self.phone_metric_var.set("Stopped")
            messagebox.showinfo("Phone not connected", "Connect the phone first, then open Control.")
            return
        address = self._sync_phone_address_from_parts()
        settings = self._phone_control_settings(address)
        self.phone_control_status_var.set("Opening phone control...")
        self.phone_metric_var.set("Connecting")

        def on_success(result: dict) -> None:
            normalized_address = str(result.get("address") or address)
            self._set_phone_address(normalized_address)
            try:
                save_phone_control_settings(self._phone_control_settings(normalized_address))
            except OSError as exc:
                self.logger.warning("Could not save phone control settings: %s", exc)
            self.phone_control_status_var.set(str(result.get("message") or "Phone control opened."))
            self.status_var.set("Phone control opened for %s." % normalized_address)
            self._set_phone_control_running(True)

        self._run_worker(
            "Opening phone control...",
            lambda: self.phone_controller.connect_and_open(
                settings.address,
                keep_screen_awake=settings.keep_screen_awake,
                turn_screen_off=settings.turn_screen_off,
                always_on_top=settings.always_on_top,
                dock_position=settings.dock_position,
                monitor_target=settings.monitor_target,
                max_size=settings.max_size,
                max_fps=settings.max_fps,
                video_bit_rate=settings.video_bit_rate,
            ),
            on_success=on_success,
            error_title="Phone control",
        )

    @staticmethod
    def _split_phone_address(address: str) -> tuple[str, str]:
        text = str(address or "").strip()
        default_port = str(DEFAULT_ADB_PORT)
        if not text:
            return "", default_port
        if ":" not in text:
            return text, default_port
        host, _separator, port = text.rpartition(":")
        if not host:
            return text, default_port
        return host.strip(), (port.strip() or default_port)

    def _set_phone_address(self, address: str) -> None:
        normalized_address = str(address or "").strip()
        phone_ip, phone_port = self._split_phone_address(normalized_address)
        self.phone_address_var.set(normalized_address)
        self.phone_ip_var.set(phone_ip)
        self.phone_port_var.set(phone_port)

    def _sync_phone_address_from_parts(self) -> str:
        phone_ip = self.phone_ip_var.get().strip()
        phone_port = self.phone_port_var.get().strip()
        if ":" in phone_ip:
            split_ip, split_port = self._split_phone_address(phone_ip)
            phone_ip = split_ip
            if not phone_port or phone_port == str(DEFAULT_ADB_PORT):
                phone_port = split_port
            self.phone_ip_var.set(phone_ip)
        if phone_ip and not phone_port:
            phone_port = str(DEFAULT_ADB_PORT)
        self.phone_port_var.set(phone_port)
        address = "%s:%s" % (phone_ip, phone_port) if phone_ip else ""
        self.phone_address_var.set(address)
        return address

    def _phone_control_settings(self, address: str | None = None) -> PhoneControlSettings:
        if address is None:
            address = self._sync_phone_address_from_parts()
        return PhoneControlSettings(
            address=address.strip(),
            keep_screen_awake=bool(self.phone_keep_screen_awake_var.get()),
            turn_screen_off=bool(self.phone_turn_screen_off_var.get()),
            always_on_top=bool(self.phone_always_on_top_var.get()),
            monitor_target=(
                "secondary"
                if self.phone_monitor_target_var.get() == "Secondary"
                else "primary"
            ),
            dock_position={
                "Dock left": "left",
                "Dock right": "right",
            }.get(self.phone_dock_position_var.get(), "off"),
            max_size=int(self.phone_max_size_var.get()),
            max_fps=int(self.phone_max_fps_var.get()),
            video_bit_rate=self.phone_video_bit_rate_var.get().replace(
                " Mbps",
                "M",
            ),
        )

    def _on_phone_control_settings_changed(self) -> None:
        try:
            save_phone_control_settings(self._phone_control_settings())
        except OSError as exc:
            self.logger.warning("Could not save phone control settings: %s", exc)
        if self.phone_controller.is_running():
            self.status_var.set("Phone control options will apply on the next connection.")

    def _stop_phone_control(self) -> None:
        self.phone_controller.close()
        if self.phone_controller.is_connected():
            self.phone_control_status_var.set("Phone connected for file transfer.")
            self.phone_metric_var.set("Connected")
            self.status_var.set("Phone control closed; file transfer remains connected.")
        else:
            self.phone_control_status_var.set("Phone control stopped")
            self.phone_metric_var.set("Stopped")
            self.status_var.set("Phone control stopped.")
        self._set_phone_control_running(False)

    def _capture_phone_screenshot(self) -> None:
        if not self.phone_controller.is_running():
            return
        if self.busy:
            self._append_telegram_event("Screenshot skipped: another action is running")
            return
        address = self._sync_phone_address_from_parts()
        output_dir = Path(self.manager.project_root).resolve() / "phone_screenshots"

        def on_success(result: dict) -> None:
            self.status_var.set(str(result.get("message") or "Screenshot saved."))

        self._run_worker(
            "Capturing phone screenshot and copying it...",
            lambda: self.phone_controller.capture_screenshot(
                address,
                output_dir,
                copy_to_clipboard=True,
            ),
            on_success=on_success,
            error_title="Phone screenshot",
        )

    def _sync_phone_control_status(self) -> None:
        if self.closing:
            return
        running = self.phone_controller.is_running()
        connected = self.phone_controller.is_connected()
        self._set_phone_control_running(running)
        if running:
            metric = "Control"
        elif connected:
            metric = "Connected"
        elif self.phone_control_status_var.get().startswith("Connection failed"):
            metric = "Error"
        elif self.phone_control_status_var.get().startswith("Connecting"):
            metric = "Connecting"
        else:
            metric = "Stopped"
        self._set_var_if_changed(self.phone_metric_var, metric)
        if not running and self.phone_control_status_var.get().startswith("Phone control opened"):
            self.phone_control_status_var.set("Phone connected for file transfer." if connected else "Phone control stopped")
        self.after(1800, self._sync_phone_control_status)

    def _set_phone_control_running(self, running: bool) -> None:
        connected = self.phone_controller.is_connected()
        ui_state = (running, connected)
        if self.phone_controls_ui_state == ui_state:
            return
        self.phone_controls_ui_state = ui_state
        if hasattr(self, "phone_connect_button"):
            self.phone_connect_button.configure(state="disabled" if running else "normal")
        if hasattr(self, "phone_control_button"):
            self.phone_control_button.configure(state="normal" if connected and not running else "disabled")
        if hasattr(self, "phone_close_button"):
            self.phone_close_button.configure(state="normal" if running else "disabled")
        if running:
            if not self.phone_screenshot_hotkey.start():
                self._append_telegram_event(
                    "Could not register screenshot hotkey %s" % SCREENSHOT_HOTKEY_LABEL
                )
            if not self.phone_close_hotkey.start():
                self._append_telegram_event(
                    "Could not register close hotkey %s" % CLOSE_HOTKEY_LABEL
                )
        else:
            self.phone_screenshot_hotkey.stop()
            self.phone_close_hotkey.stop()

    @staticmethod
    def _set_var_if_changed(variable, value) -> None:
        if variable.get() != value:
            variable.set(value)

    def _video_cut_mode_value(self) -> str:
        label = self.video_cut_mode_var.get().strip()
        return VIDEO_CUT_MODE_VALUES.get(label, "fixed")

    def _product_image_crop_ratio_value(self) -> str:
        label = self.product_image_crop_ratio_var.get().strip()
        return PRODUCT_IMAGE_CROP_RATIO_VALUES.get(label, "1:1")

    def _product_image_motion_value(self) -> str:
        label = self.product_image_motion_var.get().strip()
        return PRODUCT_IMAGE_MOTION_VALUES.get(label, "still")

    def _read_float_setting(self, variable: tk.StringVar, label: str, minimum: float, maximum: float) -> float:
        text = variable.get().strip().replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            raise ValueError("%s must be a number." % label)
        if value < minimum or value > maximum:
            raise ValueError("%s must be between %s and %s." % (label, self._format_float_setting(minimum), self._format_float_setting(maximum)))
        return value

    def _read_float_or_fallback(self, variable: tk.StringVar, fallback: float, minimum: float, maximum: float) -> float:
        try:
            value = float(variable.get().strip().replace(",", "."))
        except ValueError:
            return fallback
        return max(minimum, min(maximum, value))

    def _current_video_edit_settings(self) -> tuple[str, float, float, str, str]:
        video_cut_mode = self._video_cut_mode_value()
        fixed_chunk_duration = self._read_float_setting(self.fixed_chunk_duration_var, "Fixed chunk seconds", 0.5, 30.0)
        scene_threshold = self._read_float_setting(self.scene_threshold_var, "Scene threshold", 0.01, 0.95)
        product_image_crop_ratio = self._product_image_crop_ratio_value()
        product_image_motion = self._product_image_motion_value()
        return (
            video_cut_mode,
            fixed_chunk_duration,
            scene_threshold,
            product_image_crop_ratio,
            product_image_motion,
        )

    def _update_video_edit_controls_state(self) -> None:
        if hasattr(self, "fixed_chunk_duration_entry"):
            self.fixed_chunk_duration_entry.configure(state="normal")
        if hasattr(self, "scene_threshold_entry"):
            self.scene_threshold_entry.configure(state="normal")
        if hasattr(self, "product_image_crop_ratio_menu"):
            self.product_image_crop_ratio_menu.configure(state="normal")
        if hasattr(self, "product_image_motion_menu"):
            self.product_image_motion_menu.configure(state="normal")

    def _on_video_cut_mode_changed(self) -> None:
        self._update_video_edit_controls_state()
        self._on_video_edit_settings_changed()

    def _on_video_edit_settings_changed(self) -> None:
        self._update_video_edit_controls_state()
        if not self._save_telegram_bot_settings(show_error=True):
            return
        self.status_var.set("Video edit settings saved.")
        if self.telegram_bot_process is not None and self.telegram_bot_process.poll() is None:
            self._append_telegram_event("Video edit settings changed; restart bot to apply.")

    def _on_telegram_settings_changed(self) -> None:
        if self.telegram_add_form_visible:
            return
        self._save_telegram_bot_settings()
        self._update_telegram_target_profile_label()
        if self.telegram_bot_process is not None and self.telegram_bot_process.poll() is None:
            self.status_var.set("Telegram settings changed. Restart bot to apply the new settings.")
            self._append_telegram_event("Settings changed; restart bot to apply.")

    def _save_telegram_bot_settings(self, show_error: bool = False) -> bool:
        try:
            (
                video_cut_mode,
                fixed_chunk_duration,
                scene_threshold,
                product_image_crop_ratio,
                product_image_motion,
            ) = self._current_video_edit_settings()
            save_telegram_runtime_settings(
                TelegramRuntimeSettings(
                    bot_token=self.telegram_bot_token_var.get().strip(),
                    delivery_chat_id=self.telegram_chat_id_var.get().strip(),
                    send_result_to_telegram=bool(self.telegram_send_result_var.get()),
                    save_received_video_to_profile=bool(self.telegram_save_profile_var.get()),
                    video_cut_mode=video_cut_mode,
                    fixed_chunk_duration_seconds=fixed_chunk_duration,
                    scene_threshold=scene_threshold,
                    product_image_crop_ratio=product_image_crop_ratio,
                    product_image_motion=product_image_motion,
                )
            )
            self.config = replace(
                self.config,
                video_cut_mode=video_cut_mode,
                fixed_chunk_duration_seconds=fixed_chunk_duration,
                scene_threshold=scene_threshold,
                product_image_crop_ratio=product_image_crop_ratio,
                product_image_motion=product_image_motion,
            )
            self.fixed_chunk_duration_var.set(self._format_float_setting(fixed_chunk_duration))
            self.scene_threshold_var.set(self._format_float_setting(scene_threshold))
            self.product_image_crop_ratio_var.set(PRODUCT_IMAGE_CROP_RATIO_LABELS[product_image_crop_ratio])
            self.product_image_motion_var.set(PRODUCT_IMAGE_MOTION_LABELS[product_image_motion])
            return True
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Video edit settings", str(exc))
            return False
        except Exception as exc:
            self.logger.warning("Could not save Telegram bot settings: %s", exc)
            if show_error:
                messagebox.showerror("Settings", "Could not save settings: %s" % exc)
            return False

    def _telegram_bots_config_path(self) -> Path:
        return Path(self.manager.project_root).resolve() / "telegram_bots.json"

    def _load_telegram_bots_payload(self) -> dict:
        path = self._telegram_bots_config_path()
        if not path.exists():
            return {"bots": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Cannot read telegram_bots.json: %s" % exc)
        if isinstance(payload, list):
            return {"bots": payload}
        if isinstance(payload, dict):
            bots = payload.get("bots")
            if bots is None:
                payload["bots"] = []
                return payload
            if isinstance(bots, list):
                return payload
        raise ValueError("telegram_bots.json must contain a 'bots' list.")

    def _write_telegram_bots_payload(self, payload: dict) -> None:
        path = self._telegram_bots_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _show_telegram_add_bot_form(self) -> None:
        if self.telegram_add_form_visible:
            return
        self.telegram_add_form_previous_values = (
            self.telegram_bot_name_var.get(),
            self.telegram_bot_token_var.get(),
            self.telegram_chat_id_var.get(),
        )
        self.telegram_bot_name_var.set("")
        self.telegram_bot_token_var.set("")
        self.telegram_chat_id_var.set("")
        self.telegram_add_form_visible = True
        self.telegram_video_settings_frame.grid_remove()
        self.telegram_add_bot_frame.grid()
        self.telegram_add_bot_button.configure(state="disabled")
        self.after(20, self.telegram_bot_name_entry.focus_set)

    def _cancel_telegram_add_bot(self) -> None:
        self._close_telegram_add_bot_form()
        self.status_var.set("Add Telegram bot cancelled.")

    def _save_new_telegram_bot(self) -> None:
        if self._add_telegram_bot_config():
            self._close_telegram_add_bot_form()

    def _close_telegram_add_bot_form(self) -> None:
        previous_values = self.telegram_add_form_previous_values or ("", "", "")
        self.telegram_bot_name_var.set(previous_values[0])
        self.telegram_bot_token_var.set(previous_values[1])
        self.telegram_chat_id_var.set(previous_values[2])
        self.telegram_add_form_previous_values = None
        self.telegram_add_form_visible = False
        self.telegram_add_bot_frame.grid_remove()
        self.telegram_video_settings_frame.grid()
        self.telegram_add_bot_button.configure(state="normal" if not self.busy else "disabled")
        self._update_telegram_target_profile_label()

    def _add_telegram_bot_config(self) -> bool:
        name = self.telegram_bot_name_var.get().strip()
        bot_token = self.telegram_bot_token_var.get().strip()
        chat_id_text = self.telegram_chat_id_var.get().strip()
        if not name or not bot_token or not chat_id_text:
            messagebox.showerror("Add Telegram bot", "Nhap du 3 field: Name, Bot token, Chat ID.")
            return False
        try:
            chat_id = int(chat_id_text)
        except ValueError:
            messagebox.showerror("Add Telegram bot", "Chat ID phai la so nguyen hop le.")
            return False
        try:
            payload = self._load_telegram_bots_payload()
            bots = payload["bots"]
            normalized_name = name.lower()
            if any(str(bot.get("name") or "").strip().lower() == normalized_name for bot in bots if isinstance(bot, dict)):
                messagebox.showerror("Add Telegram bot", "Bot name da ton tai trong telegram_bots.json.")
                return False
            if any(str(bot.get("bot_token") or bot.get("token") or "").strip() == bot_token for bot in bots if isinstance(bot, dict)):
                messagebox.showerror("Add Telegram bot", "Bot token da ton tai trong telegram_bots.json.")
                return False
            bots.append({"name": name, "bot_token": bot_token, "chat_id": chat_id})
            self._write_telegram_bots_payload(payload)
        except Exception as exc:
            messagebox.showerror("Add Telegram bot", str(exc))
            return False
        self.status_var.set("Added Telegram bot %s to telegram_bots.json." % name)
        self._append_telegram_event("Added bot config: %s" % name)
        self.manager.add_log("info", "telegram_bot_added", "Added Telegram bot config: %s." % name)
        self._refresh_logs()
        return True

    def _toggle_telegram_bot(self) -> None:
        if self.telegram_bot_process is not None and self.telegram_bot_process.poll() is None:
            if self._telegram_bot_is_paused():
                self._resume_telegram_bot()
            else:
                self._pause_telegram_bot(show_status=True)
        else:
            self._start_telegram_bot()

    def _start_telegram_bot(self) -> None:
        if self.telegram_bot_process is not None and self.telegram_bot_process.poll() is None:
            self._resume_telegram_bot()
            return
        if self._is_external_telegram_bot_running():
            self.telegram_bot_process = None
            self._set_telegram_bot_button_running(True)
            self.telegram_bot_status_var.set("Bot running")
            self.status_var.set("Telegram bot is already running in another process.")
            return
        project_root = Path(self.manager.project_root).resolve()
        script_path = project_root / "start_telegram_bot.ps1"
        if not script_path.exists():
            messagebox.showerror("Telegram bot", "Khong tim thay file: %s" % script_path)
            return
        bot_token = self.telegram_bot_token_var.get().strip()
        chat_id = self.telegram_chat_id_var.get().strip()
        bots_file_exists = (project_root / "telegram_bots.json").exists()
        if not bot_token and not bots_file_exists:
            messagebox.showerror("Telegram bot", "Please enter Telegram Bot Token or add a bot to telegram_bots.json before starting.")
            self.telegram_bot_status_var.set("Bot error")
            return
        if chat_id:
            try:
                int(chat_id)
            except ValueError:
                messagebox.showerror("Telegram bot", "Telegram Chat ID must be a valid integer.")
                self.telegram_bot_status_var.set("Bot error")
                return
        effective_save = self._telegram_effective_save_to_profile()
        if not self._save_telegram_bot_settings(show_error=True):
            self.telegram_bot_status_var.set("Bot error")
            return
        (
            _video_cut_mode,
            fixed_chunk_duration,
            scene_threshold,
            product_image_crop_ratio,
            product_image_motion,
        ) = self._current_video_edit_settings()
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        pause_path = self._telegram_bot_pause_file()
        self._remove_telegram_bot_pause_file()
        stdout_log = log_dir / "telegram_bot_stdout.log"
        stderr_log = log_dir / "telegram_bot_stderr.log"
        try:
            with stdout_log.open("ab") as stdout_file, stderr_log.open("ab") as stderr_file:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                env = os.environ.copy()
                if bot_token:
                    env["AUTO_EDITOR_TELEGRAM_BOT_TOKEN"] = bot_token
                if chat_id:
                    env["AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID"] = chat_id
                if chat_id:
                    env["AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS"] = chat_id
                env["AUTO_EDITOR_TELEGRAM_INPUT_MODE"] = "simple"
                env["AUTO_EDITOR_TELEGRAM_SEND_RESULT_TO_TELEGRAM"] = "1" if self.telegram_send_result_var.get() else "0"
                env["AUTO_EDITOR_TELEGRAM_SAVE_RECEIVED_VIDEO_TO_PROFILE"] = "1" if effective_save else "0"
                env["AUTO_EDITOR_FIXED_CHUNK_DURATION_SECONDS"] = self._format_float_setting(fixed_chunk_duration)
                env["AUTO_EDITOR_SCENE_THRESHOLD"] = self._format_float_setting(scene_threshold)
                env["AUTO_EDITOR_PRODUCT_IMAGE_CROP_RATIO"] = product_image_crop_ratio
                env["AUTO_EDITOR_PRODUCT_IMAGE_MOTION"] = product_image_motion
                env["AUTO_EDITOR_TELEGRAM_PAUSE_FILE"] = str(pause_path)
                self.telegram_bot_process = subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script_path),
                    ],
                    cwd=str(project_root),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                    creationflags=creationflags,
                    close_fds=False,
                )
        except Exception as exc:
            self.telegram_bot_process = None
            self.telegram_active_profile_slug = None
            self.telegram_bot_status_var.set("Bot error")
            self._append_telegram_event("Bot error: %s" % exc)
            self.manager.add_log("error", "telegram_bot_error", "Could not start Telegram bot: %s" % exc)
            messagebox.showerror("Telegram bot", "Khong the khoi dong bot: %s" % exc)
            return
        self.telegram_active_profile_slug = None
        self._set_telegram_bot_button_running(True)
        self.telegram_bot_status_var.set("Bot running")
        self._append_telegram_event("Telegram bot started")
        self.manager.add_log("info", "telegram_bot_started", "Telegram bot started.")
        self.status_var.set("Dang khoi dong Telegram bot. Log: %s" % stdout_log)
        self.after(1500, self._sync_telegram_bot_button)

    def _telegram_bot_pause_file(self) -> Path:
        if self.telegram_bot_pause_path is None:
            self.telegram_bot_pause_path = Path(self.manager.project_root).resolve() / "logs" / "telegram_bot.pause"
        return self.telegram_bot_pause_path

    def _telegram_bot_is_paused(self) -> bool:
        return self._telegram_bot_pause_file().exists()

    def _remove_telegram_bot_pause_file(self) -> None:
        path = self._telegram_bot_pause_file()
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            self._append_telegram_event("Could not clear bot pause flag: %s" % exc)

    def _pause_telegram_bot(self, show_status: bool = True) -> None:
        process = self.telegram_bot_process
        if process is None or process.poll() is not None:
            self._set_telegram_bot_button_running(False)
            return
        path = self._telegram_bot_pause_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pause\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Telegram bot", "Khong the tam dung bot: %s" % exc)
            return
        self._set_telegram_bot_button_running(False)
        self.telegram_bot_status_var.set("Bot paused")
        self._append_telegram_event("Telegram bot paused; running jobs will continue.")
        self.manager.add_log("info", "telegram_bot_paused", "Telegram bot paused; running jobs will continue.")
        if show_status:
            self.status_var.set("Bot da tam dung nhan task moi. Cac job dang xu ly van tiep tuc chay.")

    def _resume_telegram_bot(self) -> None:
        process = self.telegram_bot_process
        if process is None or process.poll() is not None:
            self.telegram_bot_process = None
            self._start_telegram_bot()
            return
        self._remove_telegram_bot_pause_file()
        self._set_telegram_bot_button_running(True)
        self.telegram_bot_status_var.set("Bot running")
        self._append_telegram_event("Telegram bot resumed")
        self.manager.add_log("info", "telegram_bot_resumed", "Telegram bot resumed.")
        self.status_var.set("Bot da tiep tuc nhan task Telegram.")

    def _stop_telegram_bot(self, show_status: bool = True, hard: bool = False) -> None:
        process = self.telegram_bot_process
        if process is None:
            if hard:
                stopped = self._stop_external_telegram_bot_processes()
                self._remove_telegram_bot_pause_file()
                self._set_telegram_bot_button_running(False)
                self.telegram_bot_status_var.set("Bot stopped")
                if show_status:
                    self.status_var.set("Telegram bot stopped%s." % (" (%s external process tree)." % stopped if stopped else ""))
                return
            self._set_telegram_bot_button_running(False)
            return
        if not hard:
            self._pause_telegram_bot(show_status=show_status)
            return
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        self._stop_external_telegram_bot_processes()
        self.telegram_bot_process = None
        self.telegram_active_profile_slug = None
        self._remove_telegram_bot_pause_file()
        self._set_telegram_bot_button_running(False)
        self.telegram_bot_status_var.set("Bot stopped")
        self._append_telegram_event("Telegram bot stopped")
        self.manager.add_log("info", "telegram_bot_stopped", "Telegram bot stopped.")
        if show_status:
            self.status_var.set("Telegram bot stopped.")

    def _sync_telegram_bot_button(self) -> None:
        process = self.telegram_bot_process
        running = process is not None and process.poll() is None
        if not running:
            return_code = process.poll() if process is not None else None
            self.telegram_bot_process = None
            self.telegram_active_profile_slug = None
            running = self._is_external_telegram_bot_running()
            if not running:
                self._remove_telegram_bot_pause_file()
            if hasattr(self, "telegram_bot_status_var"):
                self._set_var_if_changed(
                    self.telegram_bot_status_var,
                    "Bot running" if running else ("Bot error" if return_code not in (None, 0) else "Bot stopped"),
                )
        elif self._telegram_bot_is_paused():
            self._set_var_if_changed(self.telegram_bot_status_var, "Bot paused")
        self._set_telegram_bot_button_running(running)
        if not self.closing:
            self.after(3000, self._sync_telegram_bot_button)

    def _is_external_telegram_bot_running(self) -> bool:
        return bool(self._external_telegram_bot_process_ids())

    def _external_telegram_bot_process_ids(self) -> list[int]:
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    (
                        "$selfPid=$PID; "
                        "$needleBot='auto_tiktok_editor.cli ' + 'telegram-bots'; "
                        "$needleBuiltBot='TikTokProfileManager.exe'; "
                        "$needleLauncher='start_' + 'telegram_bot.ps1'; "
                        "Get-CimInstance Win32_Process | "
                        "Where-Object { $_.ProcessId -ne $selfPid -and ("
                        "($_.Name -eq 'python.exe' -and $_.CommandLine -like \"*$needleBot*\") "
                        "-or ($_.Name -eq $needleBuiltBot -and ($_.CommandLine -like \"* telegram-bots*\" -or $_.CommandLine -like \"* telegram-bot*\")) "
                        "-or ($_.Name -eq 'powershell.exe' -and $_.CommandLine -like \"*-File*$needleLauncher*\")"
                        ") } | "
                        "Select-Object -ExpandProperty ProcessId"
                    ),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return []
        process_ids = []
        for line in str(completed.stdout or "").splitlines():
            try:
                process_ids.append(int(line.strip()))
            except (TypeError, ValueError):
                continue
        own_pid = os.getpid()
        return [process_id for process_id in process_ids if process_id != own_pid]

    def _stop_external_telegram_bot_processes(self) -> int:
        process_ids = self._external_telegram_bot_process_ids()
        stopped = 0
        for process_id in sorted(set(process_ids), reverse=True):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                stopped += 1
            except Exception:
                continue
        if stopped:
            self._append_telegram_event("Stopped %s Telegram bot process tree(s)." % stopped)
            self.manager.add_log("info", "telegram_bot_stopped", "Stopped %s Telegram bot process tree(s)." % stopped)
        return stopped

    def _set_telegram_bot_button_running(self, running: bool) -> None:
        if not hasattr(self, "telegram_bot_button"):
            return
        paused = bool(running and self._telegram_bot_is_paused())
        if running and paused:
            metric = "Paused"
        elif running:
            metric = "Running"
        elif self.telegram_bot_status_var.get() == "Bot error":
            metric = "Error"
        else:
            metric = "Stopped"
        self._set_var_if_changed(self.telegram_metric_var, metric)

        ui_state = (running, paused, metric)
        if self.telegram_controls_ui_state == ui_state:
            return
        self.telegram_controls_ui_state = ui_state
        if running and not paused:
            self.telegram_bot_button.configure(text="Start Bot", state="disabled", **_button_kwargs("primary"))
        else:
            self.telegram_bot_button.configure(text="Resume Bot" if paused else "Start Bot", state="normal", **_button_kwargs("primary"))
        if hasattr(self, "telegram_pause_button"):
            self.telegram_pause_button.configure(state="normal" if running and not paused else "disabled")
        if hasattr(self, "telegram_stop_button"):
            self.telegram_stop_button.configure(state="normal" if running else "disabled")

    def _pick_video_schedule(self) -> None:
        dialog = DateTimePickerDialog(self, self.video_detail_scheduled_var.get().strip())
        if dialog.result is None:
            return
        self.video_detail_publish_mode_var.set("scheduled")
        self.video_detail_scheduled_var.set(dialog.result)

    def _video_detail_payload(self) -> dict:
        account_label = self.video_detail_account_var.get().strip()
        publish_mode = self.video_detail_publish_mode_var.get().strip() or "now"
        scheduled_at = self.video_detail_scheduled_var.get().strip()
        if publish_mode == "now":
            scheduled_at = ""
        return {
            "caption": self.video_detail_caption_text.get("1.0", "end-1c"),
            "hashtags": self._video_detail_hashtags_text(),
            "product_id": self.video_detail_product_var.get(),
            "publish_mode": publish_mode,
            "scheduled_at": scheduled_at,
            "account_id": getattr(self, "video_account_ids_by_label", {}).get(account_label),
        }

    def _video_detail_has_changes(self, video, payload: dict) -> bool:
        return any(
            (
                (video.caption or "").strip() != (payload["caption"] or "").strip(),
                (video.hashtags or "").strip() != (payload["hashtags"] or "").strip(),
                (video.product_id or "").strip() != (payload["product_id"] or "").strip(),
                (video.publish_mode or "now") != (payload["publish_mode"] or "now"),
                (video.scheduled_at or "") != (payload["scheduled_at"] or ""),
                video.account_id != payload["account_id"],
            )
        )

    def _video_detail_validation_message(self, payload: dict) -> str:
        if payload["publish_mode"] != "scheduled":
            return ""
        scheduled_time = _parse_schedule_time(payload["scheduled_at"])
        if scheduled_time is None:
            return "Nhap thoi gian scheduled hop le truoc khi tu dong luu."
        return ""

    def _save_video_detail(self, show_errors: bool = True) -> bool:
        video_id = self.video_detail_video_id
        if video_id is None:
            return False
        video = self.manager.get_video(video_id)
        if video is None:
            if show_errors:
                messagebox.showerror("Video missing", "The selected video no longer exists.")
            return False
        payload = self._video_detail_payload()
        validation_message = self._video_detail_validation_message(payload)
        if validation_message:
            self.status_var.set(validation_message)
            return False
        if not self._video_detail_has_changes(video, payload):
            self.video_detail_dirty = False
            return True
        try:
            updated = self.manager.update_video_details(
                video.id,
                caption=payload["caption"],
                hashtags=payload["hashtags"],
                product_id=payload["product_id"],
                publish_mode=payload["publish_mode"],
                scheduled_at=payload["scheduled_at"],
                note=video.note,
                account_id=payload["account_id"],
            )
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Auto save failed", str(exc))
            else:
                self.status_var.set("Auto save failed: %s" % exc)
            return False
        self.video_detail_dirty = False
        self._refresh_videos()
        if self.video_table.exists(str(updated.id)):
            self.video_table.selection_set(str(updated.id))
        self.status_var.set("Auto saved video %s." % updated.id)
        return True

    def _set_video_schedule(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        dialog = ScheduleDialog(self, video)
        if dialog.result is None:
            return
        try:
            updated = self.manager.update_video_schedule(video.id, publish_mode=dialog.result["publish_mode"], scheduled_at=dialog.result["scheduled_at"], product_id=dialog.result["product_id"])
            self.manager.add_log("info", "video_schedule", "Video %s schedule set to %s %s." % (updated.id, updated.publish_mode, updated.scheduled_at or ""), account_id=updated.account_id, video_id=updated.id)
        except Exception as exc:
            messagebox.showerror("Update schedule failed", str(exc))
            return
        self._refresh_videos()
        self._refresh_logs()
        self.status_var.set("Updated schedule for video %s." % video.id)

    def _set_video_publish_now(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        try:
            updated = self.manager.update_video_schedule(video.id, publish_mode="now", scheduled_at="")
            self.manager.add_log("info", "video_schedule", "Video %s set to publish now." % updated.id, account_id=updated.account_id, video_id=updated.id)
        except Exception as exc:
            messagebox.showerror("Update schedule failed", str(exc))
            return
        self._refresh_videos()
        self._refresh_logs()
        self.status_var.set("Video %s set to publish now." % video.id)

    def _open_profile(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        self._run_worker("Opening profile for %s..." % account.name, lambda: self.browser.open_profile(account), on_success=lambda _result: self.status_var.set("Profile opened for %s." % account.name))

    def _mark_selected_account_live(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        try:
            updated = self.manager.update_status(account.id, "live")
            self.manager.add_log("info", "mark_live", "Marked %s as live." % updated.name, account_id=updated.id)
        except Exception as exc:
            messagebox.showerror("Mark Live failed", str(exc))
            return
        self._refresh_accounts()
        self._refresh_logs()
        if self.account_table.exists(str(updated.id)):
            self.account_table.selection_set(str(updated.id))
        self.status_var.set("Marked %s as live." % updated.name)

    def _auto_post_selected_account_videos(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if account.status != "live":
            messagebox.showinfo("Need live profile", "Profile %s phai o trang thai live truoc khi auto post." % account.name)
            return
        account_videos = [video for video in self.manager.list_videos() if video.account_id == account.id]
        if not account_videos:
            messagebox.showinfo("No videos", "Selected account has no videos to auto post.")
            return
        videos = [video for video in account_videos if self._is_video_auto_post_candidate(video)]
        if not videos:
            messagebox.showinfo("No pending videos", "Selected account has no videos ready for auto post.")
            return

        def worker() -> dict:
            counts = {}
            self.manager.add_log(
                "info",
                "auto_post_start",
                "Auto posting all videos for account: %s." % account.name,
                account_id=account.id,
            )
            for video in videos:
                fresh_video = self.manager.get_video(video.id) or video
                if fresh_video.account_id != account.id:
                    continue
                if not self._is_video_auto_post_candidate(fresh_video):
                    counts["skipped"] = counts.get("skipped", 0) + 1
                    continue
                self._post_video_for_account(account, fresh_video, counts)
                fresh_account = self.manager.get_account(account.id)
                if fresh_account is not None and fresh_account.status != "live":
                    break
            return counts

        def on_success(counts: dict) -> None:
            self._refresh_all()
            summary = self._format_counts(counts)
            self.status_var.set("Account Auto Post finished for %s: %s" % (account.name, summary))

        self._run_worker("Auto posting all videos for account: %s..." % account.name, worker, on_success=on_success)

    def _auto_post_selected_video(self) -> None:
        if not self._flush_video_detail_autosave(show_errors=True):
            return
        video = self._selected_video()
        if video is None:
            return
        address = self._phone_video_target_address()
        if address is None:
            return
        caption_text = _compose_video_caption_with_hashtags(video.caption, video.hashtags)
        if not caption_text:
            messagebox.showinfo("Missing description", "Video chua co description/hashtags de dan vao TikTok.")
            return
        product_id = str(video.product_id or "").strip()
        if not product_id:
            messagebox.showinfo("Missing Product ID", "Video chua co Product ID de tim san pham tren TikTok.")
            return
        if not product_id.isdigit():
            messagebox.showerror("Invalid Product ID", "Product ID chi duoc chua chu so: %s" % product_id)
            return
        try:
            phone_item = self._phone_video_transfer_item(video)
        except Exception as exc:
            messagebox.showerror("Video not ready", str(exc))
            return

        def worker() -> dict:
            fresh_video = self.manager.get_video(video.id) or video
            account_id = fresh_video.account_id
            try:
                self.manager.update_video_status(fresh_video.id, "queued")
                self.manager.add_log(
                    "info",
                    "phone_auto_post_start",
                    "Phone Auto Post started for video %s." % fresh_video.file_path,
                    account_id=account_id,
                    video_id=fresh_video.id,
                )
                phone_result = self.phone_controller.send_file_to_gallery(address, phone_item["file_path"])
                upload_result = self.phone_controller.open_tiktok_upload(address)
                paste_result = self.phone_controller.paste_text_with_adb_keyboard(
                    address,
                    caption_text,
                    restore_keyboard=False,
                )
                keyboard_result = self.phone_controller.press_space_and_close_keyboard(address)
                ime_result = self.phone_controller.restore_android_input_method(address)
                product_id_clipboard_result = self.phone_controller.copy_text_to_clipboard(
                    product_id,
                    label="Product ID",
                    address=address,
                    sync_to_phone=True,
                    # Product ID is the final required step.  Do not mark the
                    # video as prepared when it only reached the Windows
                    # clipboard; the phone must confirm it received the ID.
                    require_phone_clipboard=True,
                )
                return {
                    "video_id": fresh_video.id,
                    "account_id": account_id,
                    "phone_result": phone_result,
                    "upload_result": upload_result,
                    "paste_result": paste_result,
                    "keyboard_result": keyboard_result,
                    "ime_result": ime_result,
                    "product_id_clipboard_result": product_id_clipboard_result,
                }
            except Exception as exc:
                self.manager.update_video_status(fresh_video.id, "error", note=str(exc))
                self.manager.add_log(
                    "error",
                    "phone_auto_post_error",
                    "Phone Auto Post failed: %s" % exc,
                    account_id=account_id,
                    video_id=fresh_video.id,
                )
                raise

        def on_success(result: dict) -> None:
            video_id = result["video_id"]
            product_id_clipboard_result = result["product_id_clipboard_result"]
            product_id_phone_clipboard = product_id_clipboard_result.get("phone_clipboard") is True
            product_id_step_note = (
                "copied Product ID to the phone clipboard"
                if product_id_phone_clipboard
                else "could not confirm the phone clipboard; Product ID is on the Windows clipboard"
            )
            self.manager.update_video_status(
                video_id,
                "prepared",
                note="Opened TikTok publish screen, pasted description, closed keyboard, %s, then stopped for manual product selection."
                % product_id_step_note,
            )
            phone_result = result["phone_result"]
            upload_result = result["upload_result"]
            paste_result = result["paste_result"]
            keyboard_result = result["keyboard_result"]
            ime_result = result["ime_result"]
            self.manager.add_log(
                "info",
                "phone_auto_post_ready",
                "Video sent to phone, publish screen opened, description pasted, keyboard closed, %s, then automation stopped: %s."
                % (product_id_step_note, phone_result["remote_path"]),
                account_id=result["account_id"],
                video_id=video_id,
            )
            self._refresh_all()
            self.status_var.set(
                "Phone Auto Post ready for video %s. %s %s %s %s %s"
                % (
                    video_id,
                    str(upload_result.get("message") or "TikTok publish screen opened."),
                    str(paste_result.get("message") or "Description pasted."),
                    str(keyboard_result.get("message") or "Keyboard closed."),
                    str(ime_result.get("message") or "Keyboard restored."),
                    str(product_id_clipboard_result.get("message") or "Product ID copied to phone clipboard."),
                )
            )
            if product_id_phone_clipboard:
                self._show_success_notification("Đã copy Product ID vào clipboard điện thoại.")

        self._run_worker(
            "Sending video %s to phone and opening TikTok library..." % video.id,
            worker,
            on_success=on_success,
            error_title="Phone Auto Post",
        )

    def _post_video_for_account(self, account, video, counts: dict) -> None:
        try:
            validation_error = self._auto_post_validation_error(video)
            if validation_error:
                counts["skipped"] = counts.get("skipped", 0) + 1
                self.manager.update_video_status(video.id, "error", note=validation_error)
                self.manager.add_log("error", "auto_post_skipped", validation_error, account_id=account.id, video_id=video.id)
                return
            self.manager.update_video_status(video.id, "queued")
            self.manager.add_log("info", "auto_post_start", "Auto post started for video %s on %s." % (video.file_path, account.name), account_id=account.id, video_id=video.id)
            result = self.browser.post_video(account, video)
            counts[result.status] = counts.get(result.status, 0) + 1
            if result.status in ACCOUNT_STATUSES:
                self.manager.update_status(account.id, result.status)
                self.manager.update_video_status(video.id, "error", note=result.message)
                self.manager.add_log("warning", "auto_post_skipped", "%s status is %s; video not posted." % (account.name, result.status), account_id=account.id, video_id=video.id)
            else:
                self.manager.update_video_status(video.id, result.status, note=result.message)
                self.manager.add_log("info" if result.status in ("posted", "scheduled", "prepared") else "error", "auto_post_result", "%s -> %s: %s" % (account.name, result.status, result.message), account_id=account.id, video_id=video.id)
        except Exception as exc:
            counts["error"] = counts.get("error", 0) + 1
            self.manager.update_status(account.id, "error", note=str(exc))
            self.manager.update_video_status(video.id, "error", note=str(exc))
            self.manager.add_log("error", "auto_post_error", "%s -> %s" % (account.name, exc), account_id=account.id, video_id=video.id)

    def _is_video_auto_post_candidate(self, video) -> bool:
        return video.status not in ("draft", "queued", "rendering", "posted", "scheduled", "prepared") and self._video_final_path_exists(video)

    def _auto_post_validation_error(self, video) -> str | None:
        if video.account_id is None:
            return "Video %s does not have an account assigned." % video.id
        video_path = self.manager.resolve_video_path(video)
        if not self._video_final_path_exists(video):
            return "Video file does not exist: %s" % video_path
        return None

    def _format_counts(self, counts: dict) -> str:
        if not counts:
            return "no videos processed"
        return ", ".join("%s=%s" % (key, value) for key, value in sorted(counts.items()))

    def _open_tiktok_studio(self) -> None:
        account = self._selected_account()
        if account is None:
            return

        def worker() -> str:
            status = self.browser.open_tiktok_studio(account)
            if status in ACCOUNT_STATUSES:
                self.manager.update_status(account.id, status)
            return status

        def on_success(status: str) -> None:
            self._refresh_accounts()
            self.status_var.set("TikTok Studio status for %s: %s" % (account.name, status))

        self._run_worker("Opening TikTok Studio for %s..." % account.name, worker, on_success=on_success)

    def _mark_status(self, status: str) -> None:
        account = self._selected_account()
        if account is None:
            return
        try:
            self.manager.update_status(account.id, status)
        except Exception as exc:
            messagebox.showerror("Update status failed", str(exc))
            return
        self._refresh_accounts()
        self.status_var.set("Marked %s as %s." % (account.name, status))

    def _mark_video_status(self, status: str) -> None:
        video = self._selected_video()
        if video is None:
            return
        try:
            self.manager.update_video_status(video.id, status)
            self.manager.add_log("info", "video_status", "Marked video %s as %s." % (video.id, status), video_id=video.id)
        except Exception as exc:
            messagebox.showerror("Update video failed", str(exc))
            return
        self._refresh_videos()
        self._refresh_logs()
        self.status_var.set("Marked video %s as %s." % (video.id, status))

    def _selected_account_id(self) -> int | None:
        selection = self.account_table.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _selected_account(self):
        account_id = self._selected_account_id()
        if account_id is None:
            messagebox.showinfo("Select account", "Select an account first.")
            return None
        account = self.manager.get_account(account_id)
        if account is None:
            messagebox.showerror("Account missing", "The selected account no longer exists.")
        return account

    def _selected_video_id(self) -> int | None:
        selection = self.video_table.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _selected_video(self):
        video_id = self._selected_video_id()
        if video_id is None:
            messagebox.showinfo("Select video", "Select a video first.")
            return None
        video = self.manager.get_video(video_id)
        if video is None:
            messagebox.showerror("Video missing", "The selected video no longer exists.")
        return video

    def _run_worker(self, message: str, worker, on_success=None, error_title: str = "Action failed") -> None:
        if self.busy:
            messagebox.showinfo("Busy", "Another action is already running.")
            return
        if self.closing:
            return
        self.busy = True
        self._set_buttons_state("disabled")
        self._set_busy_indicator(True)
        self.status_var.set(message)
        self.browser_requests.put((worker, on_success, error_title))

    def _browser_worker_loop(self) -> None:
        while True:
            request = self.browser_requests.get()
            if request is None:
                self.browser.close_all()
                return
            worker, on_success, error_title = request
            try:
                result = worker()
                self.events.put(("success", result, on_success, error_title))
            except Exception as exc:
                self.events.put(("error", exc, None, error_title))

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload, callback, error_title = self.events.get_nowait()
                if event_type == "phone_event":
                    self._handle_phone_event(payload)
                    continue
                if event_type == "phone_screenshot_hotkey":
                    self._capture_phone_screenshot()
                    continue
                if event_type == "phone_close_hotkey":
                    self._stop_phone_control()
                    continue
                if event_type == "video_render_success":
                    self._handle_video_render_success(payload)
                    continue
                if event_type == "video_render_error":
                    self._handle_video_render_error(payload)
                    continue
                self.busy = False
                self._set_buttons_state("normal")
                self._update_source_detail_buttons()
                self._set_busy_indicator(False)
                if event_type == "success":
                    if callback is not None:
                        callback(payload)
                else:
                    self.status_var.set("Error: %s" % payload)
                    if error_title == "Phone control":
                        self.phone_control_status_var.set("Connection failed: %s" % payload)
                        self.phone_metric_var.set("Error")
                        self.manager.add_log("error", "phone_control_error", str(payload))
                        self._append_telegram_event("[phone_control_error] %s" % payload)
                        self._refresh_logs()
                    messagebox.showerror(error_title, str(payload))
                self._set_phone_control_running(self.phone_controller.is_running())
        except queue.Empty:
            pass
        self.after(150, self._poll_events)

    def _poll_database_changes(self) -> None:
        if self.closing:
            return
        try:
            videos = self.manager.list_videos()
            video_snapshot = self._video_snapshot(videos)
            if video_snapshot != self.video_snapshot:
                selected_id = self._selected_video_id()
                self._refresh_videos()
                if selected_id is not None and self.video_table.exists(str(selected_id)):
                    self.video_table.selection_set(str(selected_id))
            account_id = self._source_selected_account_id()
            sources = self.manager.list_source_channels(account_id) if account_id is not None else []
            source_snapshot = self._source_snapshot(sources)
            if source_snapshot != self.source_snapshot:
                selected_id = self._selected_source_id()
                self._refresh_sources()
                if selected_id is not None and self.source_table.exists(str(selected_id)):
                    self.source_table.selection_set(str(selected_id))
            logs = self.manager.list_logs()
            log_snapshot = self._log_snapshot(logs)
            if log_snapshot != self.log_snapshot:
                self._append_telegram_db_events(logs)
                self._refresh_logs()
        except Exception as exc:
            self.logger.warning("Auto refresh failed: %s", exc)
        self.after(2000, self._poll_database_changes)

    def _video_snapshot(self, videos) -> tuple:
        return tuple(
            (
                video.id,
                video.updated_at,
                video.status,
                getattr(video, "cut_mode", ""),
                video.file_path,
                getattr(video, "source_video_url", ""),
                getattr(video, "product_image_path", ""),
            )
            for video in videos
        )

    def _source_snapshot(self, channels) -> tuple:
        return tuple((channel.id, channel.updated_at, channel.featured) for channel in channels)

    def _log_snapshot(self, logs) -> tuple:
        return tuple((log.id, log.created_at) for log in logs)

    def _append_telegram_db_events(self, logs) -> None:
        if not hasattr(self, "telegram_event_log"):
            return
        for log in logs:
            if log.id in self.telegram_log_seen_ids:
                continue
            self.telegram_log_seen_ids.add(log.id)
            action = str(getattr(log, "action", "") or "")
            message = str(getattr(log, "message", "") or "")
            if action == "telegram_queue":
                self._append_telegram_event("Saved video to profile")
            elif "telegram" in action.lower():
                self._append_telegram_event(message or action)

    def _set_buttons_state(self, state: str) -> None:
        for button in self.action_buttons:
            button.configure(state=state)
        self.phone_controls_ui_state = None
        self.telegram_controls_ui_state = None
        if self.telegram_add_form_visible and hasattr(self, "telegram_add_bot_button"):
            self.telegram_add_bot_button.configure(state="disabled")

    def _set_busy_indicator(self, busy: bool) -> None:
        if not hasattr(self, "busy_progress"):
            return
        if busy:
            self.busy_progress.grid()
            self.busy_progress.start()
        else:
            self.busy_progress.stop()
            self.busy_progress.grid_remove()

    def _on_close(self) -> None:
        self._commit_account_hashtags_edit(show_errors=False)
        self._flush_video_detail_autosave(show_errors=False)
        self._hide_product_link_tooltip()
        self._hide_success_notification()
        self.closing = True
        self.phone_screenshot_hotkey.stop()
        self.phone_close_hotkey.stop()
        self.phone_controller.close()
        self._stop_telegram_bot(show_status=False, hard=True)
        self.video_render_executor.shutdown(wait=False, cancel_futures=True)
        self.browser_requests.put(None)
        self.browser_thread.join(timeout=5)
        self.destroy()


class TikTokProfileManagerApplication(App):
    """Backward-compatible name for older imports; the active root is CTk App."""

    def __init__(self, _legacy_root=None, manager: TikTokProfileManager | None = None, config: PipelineConfig | None = None) -> None:
        super().__init__(manager=manager, config=config)


class BaseDialog:
    def _center(self, parent) -> None:
        self.window.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = parent_x + max(0, (parent_w - width) // 2)
        y = parent_y + max(0, (parent_h - height) // 2)
        self.window.geometry("+%d+%d" % (x, y))

    def _label(self, parent, text: str, row: int) -> None:
        ctk.CTkLabel(parent, text=text, text_color=COLORS["muted"], font=(FONT, 12)).grid(row=row, column=0, sticky="w", pady=(0, 9))

    def _entry(self, parent, variable, row: int, width: int = 360, placeholder: str = ""):
        entry = ctk.CTkEntry(parent, textvariable=variable, width=width, placeholder_text=placeholder, fg_color=COLORS["input"], border_color=COLORS["border"])
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 9))
        return entry


class AccountDialog(BaseDialog):
    def __init__(self, parent) -> None:
        self.result = None
        self.window = ctk.CTkToplevel(parent)
        self.window.title("Add TikTok account")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)

        self.name_var = tk.StringVar()
        self.bot_name_var = tk.StringVar()
        self.login_type_var = tk.StringVar(value=LOGIN_TYPES[0])
        self.cut_mode_var = tk.StringVar(value=VIDEO_ROW_CUT_MODE_LABELS["original"])
        self.hashtags_var = tk.StringVar()
        self.note_var = tk.StringVar()

        frame = ctk.CTkFrame(self.window, fg_color=COLORS["surface"], corner_radius=14)
        frame.pack(fill="both", expand=True, padx=18, pady=18)
        frame.grid_columnconfigure(1, weight=1)

        self._label(frame, "Name", 0)
        self._entry(frame, self.name_var, 0, placeholder="Account name")
        self._label(frame, "Bot name", 1)
        self._entry(frame, self.bot_name_var, 1, placeholder="name in telegram_bots.json")
        self._label(frame, "Login type", 2)
        ctk.CTkOptionMenu(frame, variable=self.login_type_var, values=list(LOGIN_TYPES), fg_color=COLORS["input"], button_color=COLORS["surface_2"]).grid(row=2, column=1, sticky="ew", pady=(0, 9))
        self._label(frame, "Cut Mode", 3)
        ctk.CTkOptionMenu(
            frame,
            variable=self.cut_mode_var,
            values=list(VIDEO_ROW_CUT_MODE_VALUES.keys()),
            fg_color=COLORS["input"],
            button_color=COLORS["surface_2"],
            dropdown_fg_color=COLORS["input"],
            dropdown_hover_color=COLORS["accent"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 14),
            dropdown_font=(FONT, 14),
            dynamic_resizing=False,
            anchor="w",
        ).grid(row=3, column=1, sticky="ew", pady=(0, 9))
        self._label(frame, "Hashtags", 4)
        self._entry(frame, self.hashtags_var, 4, placeholder="#tag1 #tag2")
        self._label(frame, "Note", 5)
        self._entry(frame, self.note_var, 5, placeholder="Optional note")

        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.grid(row=6, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ctk.CTkButton(button_bar, text="Cancel", command=self.window.destroy, **_button_kwargs("secondary")).pack(side="right")
        ctk.CTkButton(button_bar, text="Add", command=self._submit, **_button_kwargs("primary")).pack(side="right", padx=(0, 8))

        self.window.bind("<Return>", lambda _event: self._submit())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._center(parent)
        self.window.wait_window()

    def _submit(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Account name is required.", parent=self.window)
            return
        self.result = {
            "name": name,
            "bot_name": self.bot_name_var.get().strip(),
            "login_type": self.login_type_var.get(),
            "cut_mode": VIDEO_ROW_CUT_MODE_VALUES.get(self.cut_mode_var.get().strip(), "original"),
            "hashtags": self.hashtags_var.get().strip() or _default_hashtag_for_account_name(name),
            "note": self.note_var.get().strip(),
        }
        self.window.destroy()


class VideoDialog(BaseDialog):
    def __init__(self, parent, file_path: str) -> None:
        self.result = None
        self.window = ctk.CTkToplevel(parent)
        self.window.title("Add video")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)

        self.file_path_var = tk.StringVar(value=file_path)
        self.caption_var = tk.StringVar()
        self.hashtags_var = tk.StringVar()
        self.product_id_var = tk.StringVar()
        self.publish_mode_var = tk.StringVar(value="now")
        self.scheduled_at_var = tk.StringVar(value=_default_schedule_time_text())
        self.note_var = tk.StringVar()

        frame = ctk.CTkFrame(self.window, fg_color=COLORS["surface"], corner_radius=14)
        frame.pack(fill="both", expand=True, padx=18, pady=18)
        frame.grid_columnconfigure(1, weight=1)

        self._label(frame, "Video file", 0)
        file_row = ctk.CTkFrame(frame, fg_color="transparent")
        file_row.grid(row=0, column=1, sticky="ew", pady=(0, 9))
        file_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(file_row, textvariable=self.file_path_var, width=520, fg_color=COLORS["input"], border_color=COLORS["border"]).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(file_row, text="Browse", width=80, command=self._browse, **_button_kwargs("secondary")).grid(row=0, column=1, padx=(8, 0))
        self._label(frame, "Caption", 1)
        self._entry(frame, self.caption_var, 1, width=520, placeholder="Description")
        self._label(frame, "Hashtags", 2)
        self._entry(frame, self.hashtags_var, 2, width=520, placeholder="#tag1 #tag2")
        self._label(frame, "Product ID", 3)
        self._entry(frame, self.product_id_var, 3, width=520, placeholder="Product ID")
        self._label(frame, "Time", 4)
        time_row = ctk.CTkFrame(frame, fg_color="transparent")
        time_row.grid(row=4, column=1, sticky="ew", pady=(0, 9))
        ctk.CTkSegmentedButton(time_row, variable=self.publish_mode_var, values=list(PUBLISH_MODES), selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"]).pack(side="left")
        ctk.CTkEntry(time_row, textvariable=self.scheduled_at_var, width=150, fg_color=COLORS["input"], border_color=COLORS["border"]).pack(side="left", padx=(10, 0))
        ctk.CTkButton(time_row, text="Pick", width=70, command=self._pick_schedule, **_button_kwargs("secondary")).pack(side="left", padx=(8, 0))
        self._label(frame, "Note", 5)
        self._entry(frame, self.note_var, 5, width=520, placeholder="Optional note")

        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.grid(row=6, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ctk.CTkButton(button_bar, text="Cancel", command=self.window.destroy, **_button_kwargs("secondary")).pack(side="right")
        ctk.CTkButton(button_bar, text="Add", command=self._submit, **_button_kwargs("primary")).pack(side="right", padx=(0, 8))

        self.window.bind("<Return>", lambda _event: self._submit())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._center(parent)
        self.window.wait_window()

    def _browse(self) -> None:
        file_path = filedialog.askopenfilename(title="Select video", filetypes=(("Video files", "*.mp4 *.mov *.m4v *.avi *.mkv"), ("All files", "*.*")))
        if file_path:
            self.file_path_var.set(file_path)

    def _pick_schedule(self) -> None:
        dialog = DateTimePickerDialog(self.window, self.scheduled_at_var.get().strip())
        if dialog.result is None:
            return
        self.publish_mode_var.set("scheduled")
        self.scheduled_at_var.set(dialog.result)

    def _submit(self) -> None:
        file_path = self.file_path_var.get().strip()
        if not file_path:
            messagebox.showerror("Missing video", "Video file is required.", parent=self.window)
            return
        if not Path(file_path).expanduser().exists():
            messagebox.showerror("Missing video", "Video file does not exist.", parent=self.window)
            return
        self.result = {
            "file_path": file_path,
            "caption": self.caption_var.get().strip(),
            "hashtags": self.hashtags_var.get().strip(),
            "product_id": self.product_id_var.get().strip(),
            "publish_mode": self.publish_mode_var.get().strip(),
            "scheduled_at": self.scheduled_at_var.get().strip(),
            "note": self.note_var.get().strip(),
        }
        self.window.destroy()


class ScheduleDialog(BaseDialog):
    def __init__(self, parent, video) -> None:
        self.result = None
        self.window = ctk.CTkToplevel(parent)
        self.window.title("Set video schedule")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        self.publish_mode_var = tk.StringVar(value=video.publish_mode or "now")
        self.scheduled_at_var = tk.StringVar(value=video.scheduled_at or _default_schedule_time_text())
        self.product_id_var = tk.StringVar(value=video.product_id or "")
        frame = ctk.CTkFrame(self.window, fg_color=COLORS["surface"], corner_radius=14)
        frame.pack(fill="both", expand=True, padx=18, pady=18)
        frame.grid_columnconfigure(1, weight=1)
        self._label(frame, "Video", 0)
        ctk.CTkLabel(frame, text=Path(video.file_path).name, text_color=COLORS["text"]).grid(row=0, column=1, sticky="w", pady=(0, 9))
        self._label(frame, "Product ID", 1)
        self._entry(frame, self.product_id_var, 1, placeholder="Product ID")
        self._label(frame, "Time", 2)
        ctk.CTkSegmentedButton(frame, variable=self.publish_mode_var, values=list(PUBLISH_MODES), selected_color=COLORS["accent"], selected_hover_color=COLORS["accent_hover"]).grid(row=2, column=1, sticky="w", pady=(0, 9))
        self._label(frame, "Scheduled At", 3)
        schedule_row = ctk.CTkFrame(frame, fg_color="transparent")
        schedule_row.grid(row=3, column=1, sticky="ew", pady=(0, 9))
        schedule_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(schedule_row, textvariable=self.scheduled_at_var, fg_color=COLORS["input"], border_color=COLORS["border"]).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(schedule_row, text="Pick", width=70, command=self._pick_schedule, **_button_kwargs("secondary")).grid(row=0, column=1, padx=(8, 0))
        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ctk.CTkButton(button_bar, text="Cancel", command=self.window.destroy, **_button_kwargs("secondary")).pack(side="right")
        ctk.CTkButton(button_bar, text="Save", command=self._submit, **_button_kwargs("primary")).pack(side="right", padx=(0, 8))
        self.window.bind("<Return>", lambda _event: self._submit())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._center(parent)
        self.window.wait_window()

    def _pick_schedule(self) -> None:
        dialog = DateTimePickerDialog(self.window, self.scheduled_at_var.get().strip())
        if dialog.result is None:
            return
        self.publish_mode_var.set("scheduled")
        self.scheduled_at_var.set(dialog.result)

    def _submit(self) -> None:
        self.result = {"product_id": self.product_id_var.get().strip(), "publish_mode": self.publish_mode_var.get().strip(), "scheduled_at": self.scheduled_at_var.get().strip()}
        self.window.destroy()


class CompactTimeInput(ctk.CTkFrame):
    """One-line HH:MM input adjusted only with mouse wheel."""

    def __init__(self, parent, hour: int = 0, minute: int = 0) -> None:
        super().__init__(
            parent,
            fg_color="transparent",
        )
        self.hour_var = tk.StringVar()
        self.minute_var = tk.StringVar()
        self._last_hour = 0
        self._last_minute = 0

        self._hour_entry = self._build_field(0, self.hour_var, "hour")
        ctk.CTkLabel(self, text=":", text_color="#CBD5E1", font=(FONT, 20, "bold"), width=14).grid(row=0, column=1, padx=5)
        self._minute_entry = self._build_field(2, self.minute_var, "minute")
        self.set_time(hour, minute)

    def _build_field(self, column: int, variable: tk.StringVar, field: str):
        entry = ctk.CTkEntry(
            self,
            textvariable=variable,
            width=78,
            height=46,
            justify="center",
            fg_color="#0B1220",
            border_color="#334155",
            border_width=1,
            corner_radius=12,
            text_color="#F8FAFC",
            font=(FONT, 19, "bold"),
        )
        entry.grid(row=0, column=column, sticky="w")
        self._bind_wheel(entry, field)
        entry.bind("<KeyPress>", self._block_key_input)
        entry.bind("<<Paste>>", self._block_edit)
        entry.bind("<<Cut>>", self._block_edit)
        entry.bind("<FocusIn>", lambda _event, widget=entry: widget.configure(border_color="#7C3AED"))
        entry.bind("<FocusOut>", lambda _event, name=field, widget=entry: self._normalize_field(name, widget))
        return entry

    def _block_key_input(self, event):
        if getattr(event, "keysym", "") in ("Tab", "ISO_Left_Tab", "Escape", "Return"):
            return None
        return "break"

    def _block_edit(self, _event):
        return "break"

    def _bind_wheel(self, widget, field: str) -> None:
        widget.bind("<MouseWheel>", lambda event, name=field: self._on_mousewheel(event, name))
        widget.bind("<Button-4>", lambda event, name=field: self._on_mousewheel(event, name, direction=1))
        widget.bind("<Button-5>", lambda event, name=field: self._on_mousewheel(event, name, direction=-1))

    def _on_mousewheel(self, event, field: str, direction: int | None = None):
        if direction is None:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        self._adjust(field, direction)
        return "break"

    def _adjust(self, field: str, direction: int) -> None:
        self.validate_time()
        hour, minute = self._current_values()
        if field == "hour":
            hour = (hour + direction) % 24
        else:
            minute = (minute + (direction * 5)) % 60
        self.set_time(hour, minute)

    def _current_values(self) -> tuple[int, int]:
        return self._last_hour, self._last_minute

    def _normalize_field(self, field: str, widget=None):
        self.validate_time()
        if widget is not None:
            widget.configure(border_color="#334155")
        if field == "hour":
            self._hour_entry.select_range(0, tk.END)
        else:
            self._minute_entry.select_range(0, tk.END)
        return "break"

    def get_time(self) -> tuple[int, int]:
        self.validate_time()
        return self._last_hour, self._last_minute

    def set_time(self, hour, minute) -> None:
        try:
            hour_value = int(hour)
            minute_value = int(minute)
        except (TypeError, ValueError):
            hour_value = self._last_hour
            minute_value = self._last_minute
        hour_value = hour_value % 24
        minute_value = ((minute_value // 5) * 5) % 60
        self._last_hour = hour_value
        self._last_minute = minute_value
        self.hour_var.set("%02d" % hour_value)
        self.minute_var.set("%02d" % minute_value)

    def validate_time(self) -> bool:
        try:
            hour_value = int(self.hour_var.get())
            minute_value = int(self.minute_var.get())
        except (TypeError, ValueError):
            self.set_time(self._last_hour, self._last_minute)
            return False
        self.set_time(hour_value, minute_value)
        return True


class DateTimePickerDialog(BaseDialog):
    def __init__(self, parent, initial_value: str = "") -> None:
        self.result = None
        self.window = ctk.CTkToplevel(parent)
        self.window.title("Pick schedule time")
        self.window.configure(fg_color="#020617")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        initial = self._snap_to_five_minutes(_parse_schedule_time(initial_value) or datetime.now() + timedelta(minutes=30))
        self.year_var = tk.StringVar(value=str(initial.year))
        self.month_var = tk.StringVar(value="%02d" % initial.month)
        self.day_var = tk.StringVar(value="%02d" % initial.day)

        frame = ctk.CTkFrame(self.window, width=560, fg_color="#111827", corner_radius=16)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.grid_columnconfigure(0, minsize=64)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Date", text_color="#94A3B8", font=(FONT, 13), width=64, height=44, anchor="w").grid(row=0, column=0, sticky="w", padx=(24, 0), pady=(24, 0))
        date_row = ctk.CTkFrame(frame, fg_color="transparent")
        date_row.grid(row=0, column=1, sticky="w", padx=(0, 24), pady=(24, 0))
        self._build_date_field(date_row, self.year_var, 88, "year")
        self._build_date_field(date_row, self.month_var, 72, "month")
        self._build_date_field(date_row, self.day_var, 72, "day")

        ctk.CTkLabel(frame, text="Time", text_color="#94A3B8", font=(FONT, 13), width=64, height=46, anchor="w").grid(row=1, column=0, sticky="w", padx=(24, 0), pady=(18, 0))
        time_row = ctk.CTkFrame(frame, fg_color="transparent")
        time_row.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(18, 0))
        self.time_stepper = CompactTimeInput(time_row, initial.hour, initial.minute)
        self.time_stepper.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            time_row,
            text="+30 min",
            width=96,
            height=44,
            corner_radius=12,
            command=self._set_default_time,
            **_button_kwargs("secondary"),
        ).grid(row=0, column=1, sticky="w", padx=(14, 0))
        ctk.CTkLabel(
            frame,
            text="Scroll over a field to adjust date and time",
            text_color="#64748B",
            font=(FONT, 11),
        ).grid(row=2, column=1, sticky="w", padx=(0, 24), pady=(6, 0))

        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=24, pady=(24, 24))
        button_bar.grid_columnconfigure(0, weight=1)
        button_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(button_bar, text="Choose", height=48, corner_radius=12, command=self._submit, **_button_kwargs("primary")).grid(row=0, column=0, sticky="ew", padx=(0, 7))
        ctk.CTkButton(button_bar, text="Cancel", height=48, corner_radius=12, command=self.window.destroy, **_button_kwargs("secondary")).grid(row=0, column=1, sticky="ew", padx=(7, 0))
        self.window.bind("<Return>", lambda _event: self._submit())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._center(parent)
        self.window.wait_window()

    def _build_date_field(self, parent, variable: tk.StringVar, width: int, field: str):
        entry = ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=width,
            height=44,
            justify="center",
            fg_color="#0B1220",
            border_color="#334155",
            border_width=1,
            corner_radius=12,
            text_color="#F8FAFC",
            font=(FONT, 16, "bold"),
        )
        entry.pack(side="left", padx=(0, 10))
        entry.bind("<MouseWheel>", lambda event, name=field: self._on_date_mousewheel(event, name))
        entry.bind("<Button-4>", lambda event, name=field: self._on_date_mousewheel(event, name, direction=1))
        entry.bind("<Button-5>", lambda event, name=field: self._on_date_mousewheel(event, name, direction=-1))
        entry.bind("<KeyPress>", self._block_key_input)
        entry.bind("<<Paste>>", self._block_edit)
        entry.bind("<<Cut>>", self._block_edit)
        entry.bind("<FocusIn>", lambda _event, widget=entry: widget.configure(border_color="#7C3AED"))
        entry.bind("<FocusOut>", lambda _event, widget=entry: widget.configure(border_color="#334155"))
        return entry

    def _block_key_input(self, event):
        if getattr(event, "keysym", "") in ("Tab", "ISO_Left_Tab", "Escape", "Return"):
            return None
        return "break"

    def _block_edit(self, _event):
        return "break"

    def _on_date_mousewheel(self, event, field: str, direction: int | None = None):
        if direction is None:
            direction = 1 if getattr(event, "delta", 0) > 0 else -1
        self._adjust_date(field, direction)
        return "break"

    def _adjust_date(self, field: str, direction: int) -> None:
        year, month, day = self._current_date_parts()
        if field == "year":
            year = max(1, min(9999, year + direction))
            day = min(day, calendar.monthrange(year, month)[1])
        elif field == "month":
            total_month = (year * 12) + (month - 1) + direction
            total_month = max(12, min(119999, total_month))
            year = total_month // 12
            month = (total_month % 12) + 1
            day = min(day, calendar.monthrange(year, month)[1])
        else:
            try:
                value = datetime(year, month, day) + timedelta(days=direction)
            except OverflowError:
                value = datetime(9999, 12, 31) if direction > 0 else datetime(1, 1, 1)
            year, month, day = value.year, value.month, value.day
        self._set_date_parts(year, month, day)

    def _current_date_parts(self) -> tuple[int, int, int]:
        try:
            year = int(self.year_var.get())
            month = int(self.month_var.get())
            day = int(self.day_var.get())
            datetime(year, month, day)
        except (TypeError, ValueError):
            fallback = datetime.now() + timedelta(minutes=30)
            year, month, day = fallback.year, fallback.month, fallback.day
        return year, month, day

    def _set_date_parts(self, year: int, month: int, day: int) -> None:
        self.year_var.set(str(year))
        self.month_var.set("%02d" % month)
        self.day_var.set("%02d" % day)

    def _snap_to_five_minutes(self, value: datetime) -> datetime:
        value = value.replace(second=0, microsecond=0)
        remainder = value.minute % 5
        if remainder:
            value += timedelta(minutes=5 - remainder)
        return value

    def _set_default_time(self) -> None:
        value = self._snap_to_five_minutes(datetime.now() + timedelta(minutes=30))
        self.year_var.set(str(value.year))
        self.month_var.set("%02d" % value.month)
        self.day_var.set("%02d" % value.day)
        self.time_stepper.set_time(value.hour, value.minute)

    def _submit(self) -> None:
        try:
            if not self.time_stepper.validate_time():
                raise ValueError("Invalid time")
            hour_value, minute_value = self.time_stepper.get_time()
            value = datetime(int(self.year_var.get()), int(self.month_var.get()), int(self.day_var.get()), hour_value, minute_value)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid schedule", "Choose a valid date and time.")
            return
        self.result = value.isoformat(sep=" ", timespec="minutes")
        self.window.destroy()


def _parse_schedule_time(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _default_schedule_time_text() -> str:
    return (datetime.now() + timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat(sep=" ", timespec="minutes")


def launch_profile_manager(
    manager: TikTokProfileManager | None = None,
    config: PipelineConfig | None = None,
) -> int:
    app = App(manager=manager, config=config)
    app.mainloop()
    return 0
