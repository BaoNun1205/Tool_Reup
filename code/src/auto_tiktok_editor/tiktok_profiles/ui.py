"""CustomTkinter UI for the TikTok Profile Manager."""

from __future__ import annotations

import calendar
import json
import logging
import os
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from auto_tiktok_editor.app.telegram_bot import TelegramBotClient
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.telegram_settings import (
    TelegramRuntimeSettings,
    load_telegram_runtime_settings,
    save_telegram_runtime_settings,
)
from auto_tiktok_editor.tiktok_profiles.models import ACCOUNT_STATUSES, LOGIN_TYPES, PUBLISH_MODES
from auto_tiktok_editor.tiktok_profiles.profile_browser import TikTokProfileBrowser
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager, normalize_hashtags, slugify


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
TELEGRAM_DOCUMENT_CAPTION_MAX_CHARS = 1024
TELEGRAM_BOT_UPLOAD_MAX_BYTES = 50 * 1000 * 1000
VIETNAM_TZ = timezone(timedelta(hours=7))
ACCOUNT_DEFAULT_HASHTAGS = {
    "linh_an_ngon": "#linhanngon",
    "an_vat_cung_tien": "#anvatcungtien",
    "my_me_an_vat": "#mymeanvat",
}
PLAY_ICON = "▶"
PLAY_HOVER_ICON = "▶"
VIDEO_CUT_MODE_LABELS = {
    "fixed": "Fixed chunks",
    "scene": "Scene changes",
    "original": "Keep Original",
}
VIDEO_CUT_MODE_VALUES = {label: value for value, label in VIDEO_CUT_MODE_LABELS.items()}
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


def _format_file_size(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % int(size)
        size /= 1024
    return "%d B" % int(size_bytes)


def _compose_video_caption_with_hashtags(caption: str, hashtags: str) -> str:
    parts = []
    clean_caption = str(caption or "").strip()
    clean_hashtags = normalize_hashtags(hashtags)
    if clean_caption:
        parts.append(clean_caption)
    if clean_hashtags:
        parts.append(clean_hashtags)
    return "\n".join(parts).strip()


def _hashtag_tokens_for_ui(value: str) -> list[str]:
    return [part for part in normalize_hashtags(value).split() if part.startswith("#")]


def _default_hashtag_for_account_name(account_name: str) -> str:
    normalized = slugify(account_name)
    compact = normalized.replace("_", "")
    if normalized in ACCOUNT_DEFAULT_HASHTAGS:
        return ACCOUNT_DEFAULT_HASHTAGS[normalized]
    for account_slug, hashtag in ACCOUNT_DEFAULT_HASHTAGS.items():
        if compact == account_slug.replace("_", ""):
            return hashtag
    return ""


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
        if kwargs:
            return super().configure(**kwargs)
        return None

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

    def _configure_columns(self) -> None:
        for column in self.columns:
            label, width = self.spec_by_column[column]
            self.tree.heading(
                column,
                text=label,
                anchor=tk.W,
                command=lambda col=column: self._sort_by_column(col),
            )
            stretch = column in self.displaycolumns
            self.tree.column(
                column,
                width=width,
                minwidth=max(64, min(width, 180)),
                anchor=tk.W,
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
        self.video_detail_dirty = False
        self.video_detail_loading = False
        self.video_detail_autosave_after = None
        self.video_detail_video_id = None
        self.video_detail_restoring_selection = False
        self.video_delete_mode = False
        self.video_delete_selection = set()
        self.busy = False
        self.closing = False
        self.action_buttons = []
        self.telegram_bot_process: subprocess.Popen | None = None
        self.telegram_active_profile_slug: str | None = None
        self.telegram_bot_pause_path: Path | None = None
        self.telegram_log_seen_ids = set()
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
        self.video_play_hover_row_id = ""

        self.title("TikTok Profile Manager")
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
        self.after(2000, self._poll_database_changes)

    def _show_ready_window(self) -> None:
        if self.closing:
            return
        self._maximize_window()
        self.deiconify()
        self.lift()

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
        nav_items = (("Accounts", "accounts"), ("Videos", "videos"), ("Logs", "logs"), ("Settings", "settings"))
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
        for index in range(3):
            metrics.grid_columnconfigure(index, weight=1, uniform="metric")
        for index, (label, variable) in enumerate(
            (("Accounts", self.account_count_var), ("Videos", self.video_count_var), ("Logs", self.log_count_var))
        ):
            card = ctk.CTkFrame(metrics, fg_color=COLORS["surface"], corner_radius=14)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0 if index == 2 else 8))
            ctk.CTkLabel(card, textvariable=variable, font=(FONT, 22, "bold"), text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(12, 0))
            ctk.CTkLabel(card, text=label, font=(FONT, 12), text_color=COLORS["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

        self.content_stack = ctk.CTkFrame(self.main, fg_color="transparent")
        self.content_stack.grid(row=1, column=0, sticky="nsew")
        self.content_stack.grid_columnconfigure(0, weight=1)
        self.content_stack.grid_rowconfigure(0, weight=1)

        self.accounts_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.videos_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.logs_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.settings_tab = ctk.CTkFrame(self.content_stack, fg_color="transparent")
        self.tab_by_name = {
            "accounts": self.accounts_tab,
            "videos": self.videos_tab,
            "logs": self.logs_tab,
            "settings": self.settings_tab,
        }
        for tab in self.tab_by_name.values():
            tab.grid(row=0, column=0, sticky="nsew")
        self.notebook = _NavigationAdapter(self)

        self._build_accounts_tab()
        self._build_videos_tab()
        self._build_logs_tab()
        self._build_settings_tab()
        self._show_tab_name("accounts")

    def build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready.")
        self.status_bar = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.status_bar.grid(row=1, column=1, sticky="ew", padx=24, pady=(8, 14))
        self.status_bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.status_bar, textvariable=self.status_var, text_color=COLORS["muted"], font=(FONT, 12), anchor="w").grid(row=0, column=0, sticky="ew")
        self.busy_progress = ctk.CTkProgressBar(self.status_bar, mode="indeterminate", width=170, progress_color=COLORS["accent_2"])
        self.busy_progress.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.busy_progress.grid_remove()

    def _card(self, parent, title: str, subtitle: str = ""):
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

    def _build_accounts_tab(self) -> None:
        self.accounts_tab.grid_columnconfigure(0, weight=1)
        self.accounts_tab.grid_rowconfigure(0, weight=1)
        self.accounts_tab.grid_rowconfigure(1, weight=0)
        card, body, actions = self._card(self.accounts_tab, "Account / Profile Management", "Profiles, login method, folder, and live status.")
        card.grid(row=0, column=0, sticky="nsew", pady=(0, 14))
        self._add_action_button(actions, "Add account", self._add_account, "primary")
        for text, command, kind in (
            ("Open TikTok Studio", self._open_tiktok_studio, "secondary"),
            ("Mark Live", self._mark_selected_account_live, "secondary"),
            ("Auto Post", self._auto_post_selected_account_videos, "primary"),
            ("Refresh", self._refresh_all, "secondary"),
        ):
            self._add_action_button(actions, text, command, kind)
        self.account_table = CTkDataTable(
            body,
            ("id", "name", "login_type", "status", "profile_path", "note", "updated_at"),
            (
                ("id", "ID", 60),
                ("name", "Name", 170),
                ("login_type", "Login", 90),
                ("status", "Status", 110),
                ("profile_path", "Profile Path", 260),
                ("note", "Note", 220),
                ("updated_at", "Updated", 170),
            ),
            on_select=self._on_account_selected,
        )
        self.account_table.configure(displaycolumns=("id", "name", "login_type", "status", "profile_path", "updated_at"))
        self.account_table.pack(fill="both", expand=True)

    def _build_settings_tab(self) -> None:
        self.settings_tab.grid_columnconfigure(0, weight=1)
        self.settings_tab.grid_rowconfigure(0, weight=0)
        self.settings_tab.grid_rowconfigure(1, weight=0)
        self._build_video_edit_settings_section()
        self._build_telegram_bot_section(self.settings_tab)

    def _build_video_edit_settings_section(self) -> None:
        card, body, actions = self._card(
            self.settings_tab,
            "Video Edit Settings",
            "Choose how the editor cuts source video and prepares product images.",
        )
        card.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._add_action_button(actions, "Save", self._on_video_edit_settings_changed, "primary")

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=1)

        mode_frame = ctk.CTkFrame(body, fg_color="transparent")
        mode_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 4))
        mode_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(mode_frame, text="Cut mode", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.video_cut_mode_menu = self._option_menu(
            mode_frame,
            self.video_cut_mode_var,
            list(VIDEO_CUT_MODE_VALUES.keys()),
            command=lambda _value: self._on_video_cut_mode_changed(),
        )
        self.video_cut_mode_menu.grid(row=1, column=0, sticky="ew")

        chunk_frame = ctk.CTkFrame(body, fg_color="transparent")
        chunk_frame.grid(row=0, column=1, sticky="ew", padx=(10, 10), pady=(0, 4))
        chunk_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(chunk_frame, text="Fixed chunk seconds", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.fixed_chunk_duration_entry = self._entry(chunk_frame, self.fixed_chunk_duration_var, "2.27")
        self.fixed_chunk_duration_entry.grid(row=1, column=0, sticky="ew")

        threshold_frame = ctk.CTkFrame(body, fg_color="transparent")
        threshold_frame.grid(row=0, column=2, sticky="ew", padx=(10, 0), pady=(0, 4))
        threshold_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(threshold_frame, text="Scene threshold", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.scene_threshold_entry = self._entry(threshold_frame, self.scene_threshold_var, "0.35")
        self.scene_threshold_entry.grid(row=1, column=0, sticky="ew")

        ratio_frame = ctk.CTkFrame(body, fg_color="transparent")
        ratio_frame.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(12, 4))
        ratio_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ratio_frame, text="Image crop ratio", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.product_image_crop_ratio_menu = self._option_menu(
            ratio_frame,
            self.product_image_crop_ratio_var,
            list(PRODUCT_IMAGE_CROP_RATIO_VALUES.keys()),
            command=lambda _value: self._on_video_edit_settings_changed(),
        )
        self.product_image_crop_ratio_menu.grid(row=1, column=0, sticky="ew")

        motion_frame = ctk.CTkFrame(body, fg_color="transparent")
        motion_frame.grid(row=1, column=1, sticky="ew", padx=(10, 10), pady=(12, 4))
        motion_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(motion_frame, text="Image motion", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.product_image_motion_menu = self._option_menu(
            motion_frame,
            self.product_image_motion_var,
            list(PRODUCT_IMAGE_MOTION_VALUES.keys()),
            command=lambda _value: self._on_video_edit_settings_changed(),
        )
        self.product_image_motion_menu.grid(row=1, column=0, sticky="ew")
        self._update_video_edit_controls_state()

    def _build_telegram_bot_section(self, parent) -> None:
        card, body, actions = self._card(
            parent,
            "Telegram Bot Management",
            "Receive natural photo captions and route completed videos.",
        )
        card.grid(row=1, column=0, sticky="ew")
        self.telegram_add_bot_button = self._add_action_button(actions, "Add", self._add_telegram_bot_config, "secondary")
        self.telegram_bot_button = self._add_action_button(actions, "Start Bot", self._start_telegram_bot, "primary")
        self.telegram_pause_button = self._add_action_button(actions, "Pause", lambda: self._pause_telegram_bot(show_status=True), "secondary")
        self.telegram_stop_button = self._add_action_button(actions, "Stop", lambda: self._stop_telegram_bot(show_status=True, hard=True), "danger")

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=1)
        body.grid_columnconfigure(3, weight=0)

        name_frame = ctk.CTkFrame(body, fg_color="transparent")
        name_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        name_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(name_frame, text="Name", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._entry(name_frame, self.telegram_bot_name_var, "Profile/bot name").grid(row=1, column=0, sticky="ew")

        token_frame = ctk.CTkFrame(body, fg_color="transparent")
        token_frame.grid(row=0, column=1, sticky="ew", padx=(10, 10), pady=(0, 10))
        token_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(token_frame, text="Bot token", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._entry(token_frame, self.telegram_bot_token_var, "Telegram Bot Token").grid(row=1, column=0, sticky="ew")

        chat_frame = ctk.CTkFrame(body, fg_color="transparent")
        chat_frame.grid(row=0, column=2, sticky="ew", padx=(10, 0), pady=(0, 10))
        chat_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(chat_frame, text="Chat ID", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._entry(chat_frame, self.telegram_chat_id_var, "Chat ID").grid(row=1, column=0, sticky="ew")

        options = ctk.CTkFrame(body, fg_color="transparent")
        options.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        self.telegram_send_switch = ctk.CTkSwitch(
            options,
            text="Send posted video/result to Telegram",
            variable=self.telegram_send_result_var,
            command=self._on_telegram_settings_changed,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 12),
        )
        self.telegram_send_switch.pack(side="left", padx=(0, 22))
        self.telegram_save_switch = ctk.CTkSwitch(
            options,
            text="Save received video to bot profile",
            variable=self.telegram_save_profile_var,
            command=self._on_telegram_settings_changed,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            text_color=COLORS["text"],
            font=(FONT, 12),
        )
        self.telegram_save_switch.pack(side="left")

        status_frame = ctk.CTkFrame(body, fg_color=COLORS["surface_2"], corner_radius=10)
        status_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 0))
        status_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(status_frame, textvariable=self.telegram_bot_status_var, text_color=COLORS["text"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 1))
        ctk.CTkLabel(status_frame, textvariable=self.telegram_target_profile_var, text_color=COLORS["muted"], font=(FONT, 12), anchor="w").grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 9))

        self.telegram_event_log = ctk.CTkTextbox(
            body,
            width=320,
            height=118,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            border_width=1,
            text_color=COLORS["text"],
            corner_radius=10,
            font=(FONT, 12),
        )
        self.telegram_event_log.grid(row=0, column=3, rowspan=3, sticky="nsew", padx=(16, 0))
        self.telegram_event_log.configure(state="disabled")
        self._append_telegram_event("Bot stopped")
        self._set_telegram_bot_button_running(False)
        self._update_telegram_target_profile_label()

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
            self._send_selected_video_to_telegram,
            "secondary",
        )
        self.video_send_button.configure(width=88)
        self.video_delete_button = self._add_action_button(
            self.video_normal_actions,
            "Delete video",
            self._start_video_delete_mode,
            "danger",
        )
        self.video_delete_button.configure(width=116)

        self.video_delete_actions = ctk.CTkFrame(table_actions, fg_color="transparent")
        self.video_delete_actions.grid(row=0, column=1, sticky="e")
        self.video_select_all_button = self._add_action_button(
            self.video_delete_actions,
            "Select all",
            self._select_all_videos_for_delete,
            "secondary",
        )
        self.video_select_all_button.configure(width=104)
        self.video_delete_selection_var = tk.StringVar(value="Selected: 0")
        self.video_delete_selection_label = ctk.CTkLabel(
            self.video_delete_actions,
            textvariable=self.video_delete_selection_var,
            width=88,
            text_color=COLORS["muted"],
            font=(FONT, 11, "bold"),
        )
        self.video_delete_selection_label.grid(row=0, column=1, padx=(12, 4))
        self.video_delete_selected_button = self._add_action_button(
            self.video_delete_actions,
            "Delete",
            self._delete_selected_videos,
            "danger",
        )
        self.video_delete_selected_button.configure(width=100)
        self.video_cancel_delete_button = self._add_action_button(
            self.video_delete_actions,
            "Cancel",
            lambda: self._set_video_delete_mode(False),
            "secondary",
        )
        self.video_cancel_delete_button.configure(width=80)
        self.video_delete_actions.grid_remove()

        columns = (
            "selected",
            "id",
            "play",
            "account_name",
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
            "id",
            "play",
            "account_name",
            "caption",
            "hashtags",
            "product_id",
            "publish_mode",
            "scheduled_at",
            "updated_at",
        )
        self.video_delete_columns = columns
        self.video_table = CTkDataTable(
            table_body,
            columns,
            (
                ("selected", "Select", 64),
                ("id", "ID", 70),
                ("play", "Play", 88),
                ("account_name", "Profile", 220),
                ("file_path", "Video File", 320),
                ("caption", "Description", 620),
                ("hashtags", "Hashtags", 280),
                ("product_id", "Product ID", 240),
                ("publish_mode", "Mode", 120),
                ("scheduled_at", "Scheduled", 220),
                ("source", "Source", 90),
                ("updated_at", "Updated", 210),
            ),
            on_select=self._on_video_selected,
            on_cell_click=self._on_video_cell_clicked,
        )
        self.video_table.configure(displaycolumns=self.video_normal_columns)
        self.video_table.pack(fill="both", expand=True)
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
                ("ID", self.video_detail_id_var),
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
        two_col.grid_columnconfigure(0, weight=1)
        two_col.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(two_col, text="Product ID", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 5))
        ctk.CTkLabel(two_col, text="Mode", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w").grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 5))
        self._entry(two_col, self.video_detail_product_var, "Product ID").grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._option_menu(
            two_col,
            variable=self.video_detail_publish_mode_var,
            values=list(PUBLISH_MODES),
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        row += 1

        ctk.CTkLabel(form, text="Scheduled", text_color=COLORS["muted"], font=(FONT, 12, "bold"), anchor="w", height=18).grid(row=row, column=0, sticky="ew", pady=(0, 4))
        schedule_frame = ctk.CTkFrame(form, fg_color="transparent")
        schedule_frame.grid(row=row + 1, column=0, sticky="ew", pady=(0, 9))
        schedule_frame.grid_columnconfigure(0, weight=1)
        self._entry(schedule_frame, self.video_detail_scheduled_var, "YYYY-MM-DD HH:MM").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(schedule_frame, text="Pick", width=70, command=self._pick_video_schedule, **_button_kwargs("secondary")).grid(row=0, column=1, padx=(8, 0))
        row += 2

    def _build_logs_tab(self) -> None:
        self.logs_tab.grid_columnconfigure(0, weight=1)
        self.logs_tab.grid_rowconfigure(0, weight=1)
        card, body, actions = self._card(self.logs_tab, "Activity log", "Recent automation events and errors.")
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
                    account.login_type,
                    account.status,
                    account.profile_path,
                    account.note,
                    _format_vietnam_datetime(account.updated_at),
                ),
            )
        if selected_id is not None and self.account_table.exists(str(selected_id)):
            self.account_table.selection_set(str(selected_id))

    def _refresh_videos(self) -> None:
        selected_id = self._selected_video_id()
        accounts = self.manager.list_accounts()
        account_names = {account.id: account.name for account in accounts}
        videos = self.manager.list_videos()
        self.video_count_var.set(str(len(videos)))
        self._refresh_video_filter_options(accounts, videos)
        visible_videos = self._filter_videos_by_account(videos)
        existing_video_ids = {video.id for video in videos}
        self.video_delete_selection.intersection_update(existing_video_ids)
        self.video_table.delete(*self.video_table.get_children())
        for video in visible_videos:
            self.video_table.insert(
                "",
                tk.END,
                iid=str(video.id),
                tags=(self._status_tag(video.status),),
                values=(
                    "[x]" if video.id in self.video_delete_selection else "[ ]",
                    video.id,
                    PLAY_ICON,
                    account_names.get(video.account_id, ""),
                    video.file_path,
                    video.caption,
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
        self._update_video_delete_buttons()

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
        self.video_delete_selection.clear()
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
        if self.video_delete_mode and column == "selected":
            try:
                self._toggle_video_delete_selection(int(row_id))
            except (TypeError, ValueError):
                return
            return
        if column == "play":
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
        if column == "play" and row_id:
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
            values[play_index] = PLAY_HOVER_ICON
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
            values = list(self.video_table.item(row_id, "values"))
            try:
                play_index = self.video_table.columns.index("play")
            except ValueError:
                play_index = -1
            if 0 <= play_index < len(values):
                values[play_index] = PLAY_ICON
            tags = (self._status_tag(video.status),) if video is not None else ()
            self.video_table.item(row_id, values=values, tags=tags)
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

    def _start_video_delete_mode(self) -> None:
        if not self.manager.list_videos():
            messagebox.showinfo("No videos", "There are no videos to delete.")
            return
        selected_id = self._selected_video_id()
        self._set_video_delete_mode(True)
        if selected_id is not None:
            self.video_delete_selection.add(selected_id)
            self._refresh_video_delete_marks()
        self.status_var.set("Select videos to delete.")

    def _set_video_delete_mode(self, enabled: bool) -> None:
        self.video_delete_mode = bool(enabled)
        if self.video_delete_mode:
            self.video_table.configure(displaycolumns=self.video_delete_columns)
            self.video_normal_actions.grid_remove()
            self.video_delete_actions.grid()
        else:
            self.video_delete_selection.clear()
            self.video_table.configure(displaycolumns=self.video_normal_columns)
            self.video_delete_actions.grid_remove()
            self.video_normal_actions.grid()
        self._refresh_video_delete_marks()
        self._update_video_delete_buttons()

    def _toggle_video_delete_selection(self, video_id: int) -> None:
        if video_id in self.video_delete_selection:
            self.video_delete_selection.remove(video_id)
        else:
            self.video_delete_selection.add(video_id)
        self._refresh_video_delete_marks()
        self._update_video_delete_buttons()

    def _select_all_videos_for_delete(self) -> None:
        self.video_delete_selection = {int(row_id) for row_id in self.video_table.get_children()}
        self._refresh_video_delete_marks()
        self._update_video_delete_buttons()

    def _refresh_video_delete_marks(self) -> None:
        if not hasattr(self, "video_table"):
            return
        for row_id in self.video_table.get_children():
            values = list(self.video_table.item(row_id, "values"))
            if not values:
                continue
            try:
                video_id = int(row_id)
            except (TypeError, ValueError):
                continue
            values[0] = "[x]" if video_id in self.video_delete_selection else "[ ]"
            self.video_table.item(row_id, values=values)

    def _update_video_delete_buttons(self) -> None:
        if not hasattr(self, "video_delete_selected_button"):
            return
        selected_count = len(self.video_delete_selection)
        self.video_delete_selection_var.set("Selected: %s" % selected_count)
        self.video_delete_selected_button.configure(
            state="normal" if selected_count else "disabled",
        )

    def _delete_selected_videos(self) -> None:
        video_ids = sorted(self.video_delete_selection)
        if not video_ids:
            messagebox.showinfo("Select videos", "Select at least one video to delete.")
            return
        message = "Delete %s selected video(s)?\n\nThis will remove the files from this computer." % len(video_ids)
        if not messagebox.askyesno("Delete videos", message):
            return
        try:
            report = self.manager.delete_videos(video_ids)
        except Exception as exc:
            messagebox.showerror("Delete video failed", str(exc))
            return
        for video_id in report["deleted_ids"]:
            self.manager.add_log("info", "video_delete", "Deleted video %s from disk and database." % video_id, video_id=video_id)
        self.video_delete_selection.clear()
        self._set_video_delete_mode(False)
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
        account_name = _account_name_from_label(self.video_detail_account_var.get())
        hashtag = _default_hashtag_for_account_name(account_name)
        if not hashtag:
            return False
        return self.video_detail_hashtags_input.add_hashtag(hashtag, notify=False)

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
            self.video_detail_id_var.set(str(video.id))
            self.video_detail_profile_var.set(account_names.get(video.account_id, "") if video.account_id is not None else "")
            self.video_detail_file_var.set(video.file_path)
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

    def _status_tag(self, value: str) -> str:
        if value in ("live", "posted", "scheduled", "prepared", "info"):
            return "live"
        if value in ("error", "product_error", "selector_error"):
            return "error"
        if value in ("need_login", "checkpoint", "no_shop", "warning"):
            return "warning"
        return "muted"

    def _refresh_all(self) -> None:
        self._refresh_accounts()
        self._refresh_videos()
        self._refresh_logs()

    def _add_account(self) -> None:
        dialog = AccountDialog(self)
        if dialog.result is None:
            return
        try:
            account = self.manager.add_account(name=dialog.result["name"], login_type=dialog.result["login_type"], note=dialog.result["note"])
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

    def _send_selected_video_to_telegram(self) -> None:
        video = self._selected_video()
        if video is None:
            return
        if video.account_id is None:
            messagebox.showerror("Send Telegram", "Video này chưa được gán profile.")
            return
        account = self.manager.get_account(video.account_id)
        if account is None:
            messagebox.showerror("Send Telegram", "Profile của video không còn tồn tại.")
            return
        try:
            video_path = self.manager.resolve_video_path(video)
            if not video_path.exists() or not video_path.is_file():
                raise ValueError("Video file does not exist: %s" % video_path)
            video_size = video_path.stat().st_size
            if video_size > TELEGRAM_BOT_UPLOAD_MAX_BYTES:
                raise ValueError(
                    "Video qua lon de gui qua Telegram bang sendDocument: %s. "
                    "Gioi han hien tai cua Bot API la khoang %s, hay nen file nho hon roi gui lai."
                    % (
                        _format_file_size(video_size),
                        _format_file_size(TELEGRAM_BOT_UPLOAD_MAX_BYTES),
                    )
                )
            bot_token, chat_id = self._telegram_target_for_account(account)
        except Exception as exc:
            messagebox.showerror("Send Telegram", str(exc))
            return
        caption = self._telegram_caption_for_video(video)
        product_id = (video.product_id or "").strip()

        def worker():
            client = TelegramBotClient(bot_token, logger=self.logger)
            client.send_document(chat_id, video_path, caption=caption, filename=video_path.name)
            if product_id:
                client.send_message(chat_id, product_id)
            return {
                "video_id": video.id,
                "account_id": account.id,
                "profile": account.name,
                "chat_id": chat_id,
                "product_id": product_id,
            }

        def on_success(payload):
            self.manager.add_log(
                "info",
                "telegram_send_video",
                "Sent video %s to Telegram chat %s for profile %s%s." % (
                    payload["video_id"],
                    payload["chat_id"],
                    payload["profile"],
                    " with product ID %s" % payload["product_id"] if payload.get("product_id") else "",
                ),
                account_id=payload["account_id"],
                video_id=payload["video_id"],
            )
            self._refresh_logs()
            self.status_var.set("Đã gửi video %s qua Telegram." % payload["video_id"])

        self._run_worker("Đang gửi video %s qua Telegram..." % video.id, worker, on_success=on_success)

    def _telegram_target_for_account(self, account) -> tuple[str, int]:
        payload = self._load_telegram_bots_payload()
        return _telegram_bot_config_for_account(payload, account)

    def _telegram_caption_for_video(self, video) -> str:
        text = _compose_video_caption_with_hashtags(video.caption, video.hashtags) or "Video da edit xong."
        if len(text) > TELEGRAM_DOCUMENT_CAPTION_MAX_CHARS:
            return text[: TELEGRAM_DOCUMENT_CAPTION_MAX_CHARS - 1].rstrip() + "..."
        return text

    def _on_account_selected(self, _event=None) -> None:
        self._update_telegram_target_profile_label()

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
        if video_cut_mode == "fixed":
            fixed_chunk_duration = self._read_float_setting(self.fixed_chunk_duration_var, "Fixed chunk seconds", 0.5, 30.0)
            scene_threshold = self._read_float_or_fallback(
                self.scene_threshold_var,
                float(getattr(self.config, "scene_threshold", 0.35)),
                0.01,
                0.95,
            )
        elif video_cut_mode == "scene":
            fixed_chunk_duration = self._read_float_or_fallback(
                self.fixed_chunk_duration_var,
                float(getattr(self.config, "fixed_chunk_duration_seconds", 2.27)),
                0.5,
                30.0,
            )
            scene_threshold = self._read_float_setting(self.scene_threshold_var, "Scene threshold", 0.01, 0.95)
        else:
            fixed_chunk_duration = self._read_float_or_fallback(
                self.fixed_chunk_duration_var,
                float(getattr(self.config, "fixed_chunk_duration_seconds", 2.27)),
                0.5,
                30.0,
            )
            scene_threshold = self._read_float_or_fallback(
                self.scene_threshold_var,
                float(getattr(self.config, "scene_threshold", 0.35)),
                0.01,
                0.95,
            )
        return (
            video_cut_mode,
            fixed_chunk_duration,
            scene_threshold,
            self._product_image_crop_ratio_value(),
            self._product_image_motion_value(),
        )

    def _update_video_edit_controls_state(self) -> None:
        mode = self._video_cut_mode_value()
        if hasattr(self, "fixed_chunk_duration_entry"):
            self.fixed_chunk_duration_entry.configure(state="normal" if mode == "fixed" else "disabled")
        if hasattr(self, "scene_threshold_entry"):
            self.scene_threshold_entry.configure(state="normal" if mode == "scene" else "disabled")

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

    def _add_telegram_bot_config(self) -> None:
        name = self.telegram_bot_name_var.get().strip()
        bot_token = self.telegram_bot_token_var.get().strip()
        chat_id_text = self.telegram_chat_id_var.get().strip()
        if not name or not bot_token or not chat_id_text:
            messagebox.showerror("Add Telegram bot", "Nhap du 3 field: Name, Bot token, Chat ID.")
            return
        try:
            chat_id = int(chat_id_text)
        except ValueError:
            messagebox.showerror("Add Telegram bot", "Chat ID phai la so nguyen hop le.")
            return
        try:
            payload = self._load_telegram_bots_payload()
            bots = payload["bots"]
            normalized_name = name.lower()
            if any(str(bot.get("name") or "").strip().lower() == normalized_name for bot in bots if isinstance(bot, dict)):
                messagebox.showerror("Add Telegram bot", "Bot name da ton tai trong telegram_bots.json.")
                return
            if any(str(bot.get("bot_token") or bot.get("token") or "").strip() == bot_token for bot in bots if isinstance(bot, dict)):
                messagebox.showerror("Add Telegram bot", "Bot token da ton tai trong telegram_bots.json.")
                return
            bots.append({"name": name, "bot_token": bot_token, "chat_id": chat_id})
            self._write_telegram_bots_payload(payload)
        except Exception as exc:
            messagebox.showerror("Add Telegram bot", str(exc))
            return
        self._save_telegram_bot_settings()
        self.telegram_bot_name_var.set("")
        self.telegram_bot_token_var.set("")
        self.telegram_chat_id_var.set("")
        self.status_var.set("Added Telegram bot %s to telegram_bots.json." % name)
        self._append_telegram_event("Added bot config: %s" % name)

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
            video_cut_mode,
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
                env["AUTO_EDITOR_VIDEO_CUT_MODE"] = video_cut_mode
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
            messagebox.showerror("Telegram bot", "Khong the khoi dong bot: %s" % exc)
            return
        self.telegram_active_profile_slug = None
        self._set_telegram_bot_button_running(True)
        self.telegram_bot_status_var.set("Bot running")
        self._append_telegram_event("Telegram bot started")
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
        self.status_var.set("Bot da tiep tuc nhan task Telegram.")

    def _stop_telegram_bot(self, show_status: bool = True, hard: bool = False) -> None:
        process = self.telegram_bot_process
        if process is None:
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
        self.telegram_bot_process = None
        self.telegram_active_profile_slug = None
        self._remove_telegram_bot_pause_file()
        self._set_telegram_bot_button_running(False)
        self.telegram_bot_status_var.set("Bot stopped")
        self._append_telegram_event("Telegram bot stopped")
        if show_status:
            self.status_var.set("Telegram bot stopped.")

    def _sync_telegram_bot_button(self) -> None:
        process = self.telegram_bot_process
        running = process is not None and process.poll() is None
        if not running:
            return_code = process.poll() if process is not None else None
            self.telegram_bot_process = None
            self.telegram_active_profile_slug = None
            self._remove_telegram_bot_pause_file()
            if hasattr(self, "telegram_bot_status_var"):
                self.telegram_bot_status_var.set("Bot error" if return_code not in (None, 0) else "Bot stopped")
        elif self._telegram_bot_is_paused():
            self.telegram_bot_status_var.set("Bot paused")
        self._set_telegram_bot_button_running(running)
        if not self.closing:
            self.after(3000, self._sync_telegram_bot_button)

    def _set_telegram_bot_button_running(self, running: bool) -> None:
        if not hasattr(self, "telegram_bot_button"):
            return
        paused = bool(running and self._telegram_bot_is_paused())
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
        if video.account_id is None:
            messagebox.showinfo("Missing profile", "Chon profile cho video trong panel ben phai truoc khi auto post.")
            return
        account = self.manager.get_account(video.account_id)
        if account is None:
            messagebox.showerror("Account missing", "Profile cua video khong con ton tai.")
            return
        if account.status != "live":
            messagebox.showinfo("Need live profile", "Profile %s phai o trang thai live truoc khi auto post." % account.name)
            return
        validation_error = self._auto_post_validation_error(video)
        if validation_error:
            messagebox.showerror("Video not ready", validation_error)
            return

        def worker() -> dict:
            counts = {}
            self.manager.add_log(
                "info",
                "auto_post_start",
                "Auto posting selected video: %s." % video.file_path,
                account_id=account.id,
                video_id=video.id,
            )
            fresh_video = self.manager.get_video(video.id) or video
            self._post_video_for_account(account, fresh_video, counts)
            return counts

        def on_success(counts: dict) -> None:
            self._refresh_all()
            self.status_var.set("Video Auto Post finished for video %s: %s" % (video.id, self._format_counts(counts)))

        self._run_worker("Auto posting selected video...", worker, on_success=on_success)

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
        return video.status not in ("posted", "scheduled", "prepared")

    def _auto_post_validation_error(self, video) -> str | None:
        if video.account_id is None:
            return "Video %s does not have an account assigned." % video.id
        video_path = self.manager.resolve_video_path(video)
        if not video_path.exists() or not video_path.is_file():
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

    def _run_worker(self, message: str, worker, on_success=None) -> None:
        if self.busy:
            messagebox.showinfo("Busy", "A browser action is already running.")
            return
        if self.closing:
            return
        self.busy = True
        self._set_buttons_state("disabled")
        self._set_busy_indicator(True)
        self.status_var.set(message)
        self.browser_requests.put((worker, on_success))

    def _browser_worker_loop(self) -> None:
        while True:
            request = self.browser_requests.get()
            if request is None:
                self.browser.close_all()
                return
            worker, on_success = request
            try:
                result = worker()
                self.events.put(("success", result, on_success))
            except Exception as exc:
                self.events.put(("error", exc, None))

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload, callback = self.events.get_nowait()
                self.busy = False
                self._set_buttons_state("normal")
                self._update_video_delete_buttons()
                self._set_busy_indicator(False)
                if event_type == "success":
                    if callback is not None:
                        callback(payload)
                else:
                    self.status_var.set("Error: %s" % payload)
                    messagebox.showerror("Browser action failed", str(payload))
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
            logs = self.manager.list_logs()
            log_snapshot = self._log_snapshot(logs)
            if log_snapshot != self.log_snapshot:
                self._append_telegram_db_events(logs)
                self._refresh_logs()
        except Exception as exc:
            self.logger.warning("Auto refresh failed: %s", exc)
        self.after(2000, self._poll_database_changes)

    def _video_snapshot(self, videos) -> tuple:
        return tuple((video.id, video.updated_at, video.status) for video in videos)

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
        self._flush_video_detail_autosave(show_errors=False)
        self._hide_product_link_tooltip()
        self.closing = True
        self._stop_telegram_bot(show_status=False, hard=True)
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
        self.login_type_var = tk.StringVar(value=LOGIN_TYPES[0])
        self.note_var = tk.StringVar()

        frame = ctk.CTkFrame(self.window, fg_color=COLORS["surface"], corner_radius=14)
        frame.pack(fill="both", expand=True, padx=18, pady=18)
        frame.grid_columnconfigure(1, weight=1)

        self._label(frame, "Name", 0)
        self._entry(frame, self.name_var, 0, placeholder="Account name")
        self._label(frame, "Login type", 1)
        ctk.CTkOptionMenu(frame, variable=self.login_type_var, values=list(LOGIN_TYPES), fg_color=COLORS["input"], button_color=COLORS["surface_2"]).grid(row=1, column=1, sticky="ew", pady=(0, 9))
        self._label(frame, "Note", 2)
        self._entry(frame, self.note_var, 2, placeholder="Optional note")

        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))
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
        self.result = {"name": name, "login_type": self.login_type_var.get(), "note": self.note_var.get().strip()}
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
