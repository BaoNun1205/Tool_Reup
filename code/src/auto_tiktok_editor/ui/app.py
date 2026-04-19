"""Giao diện Tkinter cho Auto TikTok Video Editor."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.app.telegram_bot import TelegramBotService
from auto_tiktok_editor.app.telegram_delivery import TelegramDeliveryService
from auto_tiktok_editor.commercial_runtime import configure_tk_environment, ensure_runtime_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionEvent, SessionItemSpec, SessionResult, SessionSpec
from auto_tiktok_editor.license.guard import LicenseGuard
from auto_tiktok_editor.license.models import VerifiedLicenseSession
from auto_tiktok_editor.telegram_settings import TelegramRuntimeSettings, clear_telegram_runtime_settings, save_telegram_runtime_settings
from auto_tiktok_editor.ui.license_dialog import ensure_ui_license_session


STAGE_LABELS = {
    "validating_input": "Đang kiểm tra dữ liệu",
    "downloading_source": "Đang tải video TikTok",
    "normalizing_media": "Đang chuẩn hóa video",
    "speed_processing": "Đang tăng tốc 1.2x",
    "detecting_scenes": "Đang cắt video thành đoạn",
    "planning_edit": "Đang xáo và sắp thứ tự",
    "rendering_final": "Đang render video final",
    "exporting_artifacts": "Đang xuất file đầu ra",
}

STATUS_LABELS = {
    "draft": "Nháp",
    "invalid": "Dữ liệu lỗi",
    "queued": "Đang chờ",
    "validating": "Đang kiểm tra",
    "downloading": "Đang tải",
    "processing": "Đang xử lý",
    "completed": "Hoàn tất",
    "failed": "Thất bại",
    "validating_session": "Đang kiểm tra session",
    "ready_to_run": "Sẵn sàng chạy",
    "running": "Đang chạy",
    "completed_with_success": "Hoàn tất toàn bộ",
    "completed_with_partial_failure": "Xong nhưng có item lỗi",
    "failed_session": "Session thất bại",
}

PALETTE = {
    "bg": "#08111F",
    "hero": "#0F1B2E",
    "card": "#101C30",
    "card_alt": "#14223A",
    "border": "#243755",
    "text": "#F5F7FB",
    "muted": "#91A3BF",
    "accent": "#1ED3A5",
    "accent_hover": "#19B88F",
    "accent_2": "#63A9FF",
    "input_bg": "#182740",
    "log_bg": "#07101D",
}

STATUS_COLORS = {
    "draft": ("#1C2942", "#D8E4F7"),
    "invalid": ("#4A1F2C", "#FFD0D6"),
    "queued": ("#20334B", "#D4E1F5"),
    "validating": ("#4B3921", "#F7DCA5"),
    "downloading": ("#17394A", "#B7E5F4"),
    "processing": ("#183A2C", "#C9F2D6"),
    "completed": ("#163C2B", "#D4F7DF"),
    "failed": ("#4A1F2C", "#FFD0D6"),
    "validating_session": ("#4B3921", "#F7DCA5"),
    "ready_to_run": ("#20334B", "#D4E1F5"),
    "running": ("#20334B", "#D4E1F5"),
    "completed_with_success": ("#163C2B", "#D4F7DF"),
    "completed_with_partial_failure": ("#4B3921", "#F7DCA5"),
    "failed_session": ("#4A1F2C", "#FFD0D6"),
}

SUPPORTED_BULK_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class SessionRowWidgets:
    row_id: str
    frame: ttk.Frame
    index_var: tk.StringVar
    url_var: tk.StringVar
    image_var: tk.StringVar
    opacity_var: tk.IntVar
    opacity_label_var: tk.StringVar
    status_var: tk.StringVar
    detail_var: tk.StringVar
    url_entry: ttk.Entry
    image_entry: ttk.Entry
    opacity_scale: tk.Scale
    browse_button: ttk.Button
    remove_button: ttk.Button
    open_button: ttk.Button
    rerun_button: ttk.Button
    status_chip: tk.Label
    output_dir: Optional[str] = None
    preview_video_path: Optional[str] = None


class EditorApplication(object):
    def __init__(
        self,
        root: tk.Tk,
        config: Optional[PipelineConfig] = None,
        orchestrator: Optional[SessionOrchestrator] = None,
        logger: Optional[logging.Logger] = None,
        license_guard: Optional[LicenseGuard] = None,
        license_session: Optional[VerifiedLicenseSession] = None,
    ):
        self.root = root
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor.ui")
        self.license_guard = license_guard
        self.license_session = license_session
        self.orchestrator = orchestrator or SessionOrchestrator(
            self.config,
            license_checkpoint=self._license_runtime_checkpoint if self.license_guard is not None else None,
            logger=self.logger,
        )
        self.event_queue = queue.Queue()
        self.session_rerun_queue = queue.Queue()
        self.rows: List[SessionRowWidgets] = []
        self.row_counter = 0
        self.running = False
        self.latest_result: Optional[SessionResult] = None
        self.current_session_dir: Optional[str] = None
        self.review_ready = False
        self.reauthenticate_requested = False
        self.license_reauthentication_required = False
        self.license_warning_message = ""
        self.bulk_import_window: Optional[tk.Toplevel] = None
        self.telegram_bot_service = None  # type: Optional[TelegramBotService]
        self.telegram_bot_thread = None  # type: Optional[threading.Thread]
        self.telegram_delivery = None  # type: Optional[TelegramDeliveryService]
        self.telegram_token_entry = None
        self.telegram_chat_id_entry = None
        self.telegram_status_label = None
        self.session_name_var = tk.StringVar()
        self.output_root_var = tk.StringVar(value=str(self.config.default_output_root))
        self.telegram_bot_token_var = tk.StringVar(value=str(self.config.telegram_bot_token or ""))
        self.telegram_chat_id_var = tk.StringVar(value=str(self.config.telegram_delivery_chat_id or ""))
        self.telegram_status_var = tk.StringVar(value=self._telegram_status_text())
        self.license_summary_var = tk.StringVar(value=self._license_summary_text())
        self.session_status_var = tk.StringVar(value=STATUS_LABELS["draft"])
        self.session_detail_var = tk.StringVar(value="Tạo danh sách item rồi bấm Chạy session.")
        self.summary_counts_var = tk.StringVar(value="0 item | 0 hoàn tất | 0 lỗi")
        self.summary_path_var = tk.StringVar(value="Chưa có output session.")
        self._configure_window()
        self._build_styles()
        self._build_layout()
        if self._supports_local_telegram_configuration():
            self.finalize_button.configure(text="Gửi qua Telegram", command=self._send_session_via_telegram)
            self._start_embedded_telegram_bot_if_configured()
        self._add_row()
        self.root.after(self.config.ui_poll_interval_ms, self._poll_events)
        if self.license_guard is not None:
            self.root.after(
                max(30, int(self.license_guard.config.heartbeat_interval_seconds)) * 1000,
                self._poll_license_heartbeat,
            )

    def _configure_window(self) -> None:
        self.root.title("Auto TikTok Video Editor")
        self.root.geometry("1460x900")
        self.root.minsize(1220, 780)
        self.root.configure(bg=PALETTE["bg"])

    def _start_embedded_telegram_bot_if_configured(self) -> None:
        runtime_config = self._runtime_telegram_config()
        if not runtime_config.allow_local_telegram:
            return
        if not runtime_config.telegram_bot_token or self.telegram_bot_thread is not None:
            return
        license_guard = getattr(self, "license_guard", None)
        try:
            self.telegram_bot_service = TelegramBotService(
                config=runtime_config,
                runtime_checkpoint=self._license_runtime_checkpoint if license_guard is not None else None,
                logger=self.logger,
            )
        except Exception as exc:
            self.logger.exception("Unable to start embedded Telegram bot.")
            self._append_log("Không thể khởi động bot Telegram nền: %s" % exc)
            return
        self.telegram_bot_thread = threading.Thread(
            target=self.telegram_bot_service.serve_forever,
            daemon=True,
            name="auto-editor-telegram-bot",
        )
        self.telegram_bot_thread.start()
        if hasattr(self, "telegram_status_var"):
            self.telegram_status_var.set("Bot Telegram riêng của bạn đang chạy nền.")
        self._append_log("Bot Telegram nền đã được khởi động cùng UI.")

    def _supports_local_telegram_configuration(self) -> bool:
        return bool(self.config.allow_local_telegram or self.config.commercial_mode or self.config.telegram_bot_token)

    def _runtime_telegram_config(self) -> PipelineConfig:
        bot_token = self.telegram_bot_token_var.get().strip() if hasattr(self, "telegram_bot_token_var") else str(self.config.telegram_bot_token or "").strip()
        delivery_chat_id = self.telegram_chat_id_var.get().strip() if hasattr(self, "telegram_chat_id_var") else str(self.config.telegram_delivery_chat_id or "").strip()
        allowed_chat_ids = ()
        if delivery_chat_id:
            try:
                allowed_chat_ids = (int(delivery_chat_id),)
            except ValueError:
                allowed_chat_ids = ()
        return replace(
            self.config,
            allow_local_telegram=bool(bot_token),
            telegram_bot_token=bot_token,
            telegram_delivery_chat_id=delivery_chat_id,
            telegram_allowed_chat_ids=allowed_chat_ids,
        )

    def _telegram_status_text(self) -> str:
        if self.config.telegram_bot_token:
            if getattr(self, "telegram_bot_thread", None) is not None:
                return "Bot Telegram riêng của bạn đang chạy nền."
            return "Đã có cấu hình Telegram riêng. App sẽ tự khởi động bot khi mở."
        return "Nhập bot token và chat ID của riêng bạn để bật Telegram trên máy này."

    def _save_telegram_settings(self) -> None:
        token = self.telegram_bot_token_var.get().strip()
        delivery_chat_id = self.telegram_chat_id_var.get().strip()
        if not token:
            clear_telegram_runtime_settings()
            self.config = replace(
                self.config,
                allow_local_telegram=False,
                telegram_bot_token="",
                telegram_delivery_chat_id="",
                telegram_allowed_chat_ids=(),
            )
            self._stop_embedded_telegram_bot()
            self.telegram_status_var.set("Đã tắt Telegram riêng trên máy này.")
            self._append_log("Đã xóa cấu hình Telegram riêng khỏi máy này.")
            return
        if self._normalize_telegram_chat_id(delivery_chat_id) is None:
            messagebox.showerror("Telegram Chat ID chưa hợp lệ", "Nhập Telegram Chat ID dạng số nguyên hợp lệ.")
            return
        save_telegram_runtime_settings(
            TelegramRuntimeSettings(
                bot_token=token,
                delivery_chat_id=delivery_chat_id,
            )
        )
        self.config = self._runtime_telegram_config()
        self.telegram_status_var.set("Đã lưu cấu hình Telegram riêng. Bot sẽ được khởi động trên máy này.")
        self._append_log("Đã lưu cấu hình Telegram riêng cho khách hàng hiện tại.")
        self._restart_embedded_telegram_bot()

    def _stop_embedded_telegram_bot(self) -> None:
        if self.telegram_bot_service is not None:
            try:
                self.telegram_bot_service.stop()
            except Exception:
                self.logger.exception("Unable to stop embedded Telegram bot cleanly.")
        if self.telegram_bot_thread is not None and self.telegram_bot_thread.is_alive():
            self.telegram_bot_thread.join(timeout=1.0)
        self.telegram_bot_service = None
        self.telegram_bot_thread = None

    def _restart_embedded_telegram_bot(self) -> None:
        self._stop_embedded_telegram_bot()
        self._start_embedded_telegram_bot_if_configured()

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=PALETTE["bg"])
        style.configure("Hero.TFrame", background=PALETTE["hero"])
        style.configure("Card.TFrame", background=PALETTE["card"])
        style.configure("Alt.TFrame", background=PALETTE["card_alt"])
        style.configure("Panel.TLabelframe", background=PALETTE["card"], borderwidth=1, relief="solid", bordercolor=PALETTE["border"], lightcolor=PALETTE["border"], darkcolor=PALETTE["border"])
        style.configure("Panel.TLabelframe.Label", background=PALETTE["card"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 11, "bold"))
        style.configure("HeroTitle.TLabel", background=PALETTE["hero"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 27, "bold"))
        style.configure("HeroText.TLabel", background=PALETTE["hero"], foreground=PALETTE["muted"], font=("Segoe UI", 11))
        style.configure("Head.TLabel", background=PALETTE["card"], foreground=PALETTE["text"], font=("Segoe UI Semibold", 11, "bold"))
        style.configure("Body.TLabel", background=PALETTE["card"], foreground=PALETTE["muted"], font=("Segoe UI", 10))
        style.configure("AltBody.TLabel", background=PALETTE["card_alt"], foreground=PALETTE["muted"], font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10, "bold"), padding=(18, 11), background=PALETTE["accent"], foreground="#06251F", borderwidth=0)
        style.map("Primary.TButton", background=[("active", PALETTE["accent_hover"]), ("disabled", "#1E3640")], foreground=[("disabled", "#7BA79F")])
        style.configure("Secondary.TButton", font=("Segoe UI Semibold", 10), padding=(14, 9), background=PALETTE["card_alt"], foreground=PALETTE["text"], borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#1A3150"), ("disabled", "#152435")], foreground=[("disabled", "#6E81A1")])
        style.configure("Ghost.TButton", font=("Segoe UI", 9), padding=(10, 7), background=PALETTE["card_alt"], foreground=PALETTE["muted"], borderwidth=0)
        style.map("Ghost.TButton", background=[("active", "#1A3150"), ("disabled", "#152435")], foreground=[("disabled", "#6E81A1")])
        style.configure("TEntry", padding=10, fieldbackground=PALETTE["input_bg"], background=PALETTE["input_bg"], foreground=PALETTE["text"], insertcolor=PALETTE["text"], bordercolor=PALETTE["border"], lightcolor=PALETTE["border"], darkcolor=PALETTE["border"], relief="solid")
        style.map("TEntry", bordercolor=[("focus", PALETTE["accent_2"])], lightcolor=[("focus", PALETTE["accent_2"])], darkcolor=[("focus", PALETTE["accent_2"])])
        style.configure("Vertical.TScrollbar", background=PALETTE["card_alt"], troughcolor=PALETTE["log_bg"], bordercolor=PALETTE["border"], arrowcolor=PALETTE["muted"])

    def _default_blur_percent(self) -> int:
        return self._blur_percent_from_fade_ratio(self.config.split_separator_fade_ratio)

    def _blur_percent_from_fade_ratio(self, value: float) -> int:
        clamped = max(0.05, min(0.95, float(value)))
        return int(round(clamped * 100.0))

    def _fade_ratio_from_blur_percent(self, value: int) -> float:
        value = max(5, min(95, int(value)))
        return max(0.05, min(0.95, value / 100.0))

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=24)
        outer.pack(fill="both", expand=True)

        hero = ttk.Frame(outer, style="Hero.TFrame", padding=26)
        hero.pack(fill="x")
        hero_header = ttk.Frame(hero, style="Hero.TFrame")
        hero_header.pack(fill="x")
        ttk.Label(hero_header, text="Auto TikTok Video Editor", style="HeroTitle.TLabel").pack(side="left", anchor="w")
        self.logout_button = ttk.Button(hero_header, text="Đăng xuất", style="Secondary.TButton", command=self._logout_and_relogin)
        self.logout_button.pack(side="right", anchor="e")
        ttk.Label(
            hero,
            text="Nhập nhiều cặp link TikTok và ảnh sản phẩm, rồi chạy session tuần tự với trạng thái rõ cho từng item.",
            style="HeroText.TLabel",
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        if self.license_session is not None:
            ttk.Label(
                hero,
                textvariable=self.license_summary_var,
                style="HeroText.TLabel",
                wraplength=920,
                justify="left",
            ).pack(anchor="w", pady=(12, 0))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(18, 0))

        left_column = ttk.Frame(body, style="App.TFrame")
        right_column = ttk.Frame(body, style="App.TFrame")
        body.add(left_column, weight=5)
        body.add(right_column, weight=3)

        controls = ttk.LabelFrame(left_column, text="Thiết lập session", style="Panel.TLabelframe", padding=18)
        controls.pack(fill="x")
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)
        ttk.Label(controls, text="Tên session", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Thư mục output", style="Head.TLabel").grid(row=0, column=1, columnspan=2, sticky="w")

        self.session_name_entry = ttk.Entry(controls, textvariable=self.session_name_var)
        self.session_name_entry.grid(row=1, column=0, sticky="ew", padx=(0, 14), pady=(8, 0))

        self.output_root_entry = ttk.Entry(controls, textvariable=self.output_root_var)
        self.output_root_entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))

        self.output_browse_button = ttk.Button(
            controls,
            text="Chọn thư mục",
            style="Secondary.TButton",
            command=self._browse_output_root,
        )
        self.output_browse_button.grid(row=1, column=2, sticky="e", padx=(14, 0), pady=(8, 0))

        actions = ttk.Frame(controls, style="Card.TFrame")
        actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.add_row_button = ttk.Button(actions, text="+ Thêm dòng", style="Secondary.TButton", command=self._add_row)
        self.add_row_button.grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(actions, text="Chạy session", style="Primary.TButton", command=self._start_session)
        self.bulk_import_button = ttk.Button(actions, text="Nh\u1eadp h\u00e0ng lo\u1ea1t", style="Secondary.TButton", command=self._open_bulk_import_dialog)
        self.bulk_import_button.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.run_button.grid(row=0, column=2, sticky="e")

        left_panel = ttk.LabelFrame(left_column, text="Danh sách item", style="Panel.TLabelframe", padding=14)
        left_panel.pack(fill="both", expand=True, pady=(16, 0))
        self._build_rows_panel(left_panel)
        self._build_summary_panel(right_column)
    def _build_rows_panel(self, parent: ttk.LabelFrame) -> None:
        container = ttk.Frame(parent, style="Card.TFrame")
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, background=PALETTE["card"], highlightthickness=0, bd=0)
        self.rows_canvas = canvas
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.rows_host = ttk.Frame(canvas, style="Card.TFrame")
        self.rows_host.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        self.rows_window_id = canvas.create_window((0, 0), window=self.rows_host, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(self.rows_window_id, width=event.width))
        self._bind_rows_mousewheel(canvas)
        self._bind_rows_mousewheel(self.rows_host)

    def _build_summary_panel(self, parent: ttk.Frame) -> None:
        summary = ttk.LabelFrame(parent, text="Tổng quan session", style="Panel.TLabelframe", padding=0)
        summary.pack(fill="both", expand=True)
        container = ttk.Frame(summary, style="Card.TFrame")
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, background=PALETTE["card"], highlightthickness=0, bd=0)
        self.summary_canvas = canvas
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.summary_host = ttk.Frame(canvas, style="Card.TFrame", padding=16)
        self.summary_host.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        self.summary_window_id = canvas.create_window((0, 0), window=self.summary_host, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(self.summary_window_id, width=event.width))
        self._bind_summary_mousewheel(canvas)
        self._bind_summary_mousewheel(self.summary_host)
        self.session_chip = None
        ttk.Label(self.summary_host, text="Tổng quan", style="Head.TLabel").pack(anchor="w")
        ttk.Label(self.summary_host, textvariable=self.summary_counts_var, style="Body.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(self.summary_host, text="Output session", style="Head.TLabel").pack(anchor="w", pady=(18, 0))
        ttk.Label(self.summary_host, textvariable=self.summary_path_var, style="Body.TLabel", wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))
        if self._supports_local_telegram_configuration():
            ttk.Label(self.summary_host, text="Telegram Bot Token", style="Head.TLabel").pack(anchor="w", pady=(18, 0))
            self.telegram_token_entry = ttk.Entry(self.summary_host, textvariable=self.telegram_bot_token_var)
            self.telegram_token_entry.pack(fill="x", pady=(8, 0))
            ttk.Label(self.summary_host, text="Telegram Chat ID", style="Head.TLabel").pack(anchor="w", pady=(14, 0))
            self.telegram_chat_id_entry = ttk.Entry(self.summary_host, textvariable=self.telegram_chat_id_var)
            self.telegram_chat_id_entry.pack(fill="x", pady=(8, 0))
            ttk.Button(
                self.summary_host,
                text="Lưu cấu hình Telegram",
                style="Secondary.TButton",
                command=self._save_telegram_settings,
            ).pack(anchor="w", pady=(12, 0))
            self.telegram_status_label = ttk.Label(
                self.summary_host,
                textvariable=self.telegram_status_var,
                style="Body.TLabel",
                wraplength=360,
                justify="left",
            )
            self.telegram_status_label.pack(anchor="w", pady=(10, 0))
        action_row = ttk.Frame(self.summary_host, style="Card.TFrame")
        action_row.pack(fill="x", pady=(10, 0))
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        self.open_session_button = ttk.Button(action_row, text="Mở thư mục session", style="Secondary.TButton", command=lambda: self._open_path(self.current_session_dir), state="disabled")
        self.open_session_button.grid(row=0, column=0, sticky="w")
        self.finalize_button = ttk.Button(action_row, text="OK lưu vào output", style="Primary.TButton", command=self._approve_session_outputs, state="disabled")
        self.finalize_button.grid(row=0, column=1, sticky="e")

        log_box = ttk.LabelFrame(self.summary_host, text="Nhật ký hoạt động", style="Panel.TLabelframe", padding=14)
        log_box.pack(fill="x", expand=True, pady=(18, 0))
        self.log_text = tk.Text(log_box, height=20, wrap="word", relief="flat", bg=PALETTE["log_bg"], fg=PALETTE["text"], insertbackground=PALETTE["text"], font=("Consolas", 10), padx=12, pady=12)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
    def _add_row(self) -> None:
        self.row_counter += 1
        row_id = "row_%03d" % self.row_counter
        frame = ttk.Frame(self.rows_host, style="Alt.TFrame", padding=18)
        frame.pack(fill="x", pady=(0, 14))
        frame.columnconfigure(0, weight=1)
        index_var = tk.StringVar(value="Item %d" % (len(self.rows) + 1))
        url_var = tk.StringVar()
        image_var = tk.StringVar()
        default_blur_percent = self._default_blur_percent()
        opacity_var = tk.IntVar(value=default_blur_percent)
        opacity_label_var = tk.StringVar(value="%d%%" % default_blur_percent)
        status_var = tk.StringVar(value=STATUS_LABELS["draft"])
        detail_var = tk.StringVar(value="Chưa chạy.")

        header = ttk.Frame(frame, style="Alt.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=index_var, style="Head.TLabel").grid(row=0, column=0, sticky="w")
        action_box = ttk.Frame(header, style="Alt.TFrame")
        action_box.grid(row=0, column=1, sticky="e")
        open_button = ttk.Button(action_box, text="Xem video", style="Ghost.TButton", state="disabled", command=lambda rid=row_id: self._open_row_output(rid))
        open_button.pack(side="left")
        rerun_button = ttk.Button(action_box, text="Tạo lại", style="Ghost.TButton", state="disabled", command=lambda rid=row_id: self._rerun_row(rid))
        rerun_button.pack(side="left", padx=(8, 0))
        remove_button = ttk.Button(action_box, text="Xóa dòng", style="Ghost.TButton", command=lambda rid=row_id: self._remove_row(rid))
        remove_button.pack(side="left", padx=(8, 0))

        ttk.Label(frame, text="Link TikTok public", style="AltBody.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 6))
        url_entry = ttk.Entry(frame, textvariable=url_var)
        url_entry.grid(row=2, column=0, sticky="ew")

        ttk.Label(frame, text="Ảnh sản phẩm", style="AltBody.TLabel").grid(row=3, column=0, sticky="w", pady=(14, 6))
        image_box = ttk.Frame(frame, style="Alt.TFrame")
        image_box.grid(row=4, column=0, sticky="ew")
        image_box.columnconfigure(0, weight=1)
        image_entry = ttk.Entry(image_box, textvariable=image_var)
        image_entry.grid(row=0, column=0, sticky="ew")
        browse_button = ttk.Button(image_box, text="Chọn ảnh", style="Secondary.TButton", command=lambda rid=row_id: self._browse_image(rid))
        browse_button.grid(row=0, column=1, padx=(10, 0))

        slider_head = ttk.Frame(frame, style="Alt.TFrame")
        slider_head.grid(row=5, column=0, sticky="ew", pady=(14, 6))
        slider_head.columnconfigure(0, weight=1)
        ttk.Label(slider_head, text="Độ mờ vùng giao nhau", style="AltBody.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(slider_head, textvariable=opacity_label_var, style="Head.TLabel").grid(row=0, column=1, sticky="e")
        opacity_scale = tk.Scale(frame, from_=5, to=95, orient="horizontal", showvalue=False, highlightthickness=0, bd=0, relief="flat", variable=opacity_var, background=PALETTE["card_alt"], foreground=PALETTE["text"], troughcolor=PALETTE["input_bg"], activebackground=PALETTE["accent_2"], length=460, command=lambda value, label=opacity_label_var: label.set("%d%%" % int(round(float(value)))))
        opacity_scale.grid(row=6, column=0, sticky="ew")

        footer = ttk.Frame(frame, style="Alt.TFrame")
        footer.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(1, weight=1)
        status_chip = tk.Label(footer, text=STATUS_LABELS["draft"], font=("Segoe UI Semibold", 9, "bold"), padx=12, pady=6, bd=0)
        status_chip.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=detail_var, style="AltBody.TLabel", wraplength=720, justify="left").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._set_chip_style(status_chip, "draft")

        self.rows.append(SessionRowWidgets(row_id, frame, index_var, url_var, image_var, opacity_var, opacity_label_var, status_var, detail_var, url_entry, image_entry, opacity_scale, browse_button, remove_button, open_button, rerun_button, status_chip))
        self._bind_rows_mousewheel(frame)
        self._renumber_rows()

    def _remove_row(self, row_id: str) -> None:
        if self.running:
            return
        if len(self.rows) == 1:
            row = self._find_row_by_id(row_id)
            if row:
                row.url_var.set("")
                row.image_var.set("")
                row.output_dir = None
                row.preview_video_path = None
                row.open_button.configure(state="disabled")
                row.rerun_button.configure(state="disabled")
                self._set_row_status(row, "draft", "Chưa chạy.")
            return
        for index, row in enumerate(self.rows):
            if row.row_id == row_id:
                row.frame.destroy()
                self.rows.pop(index)
                break
        self._renumber_rows()

    def _renumber_rows(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            row.index_var.set("Item %d" % index)

    def _browse_image(self, row_id: str) -> None:
        path = filedialog.askopenfilename(title="Chọn ảnh sản phẩm", filetypes=[("Image files", "*.png;*.jpg;*.jpeg"), ("PNG", "*.png"), ("JPEG", "*.jpg;*.jpeg")])
        if path:
            row = self._find_row_by_id(row_id)
            if row:
                row.image_var.set(path)

    def _browse_output_root(self) -> None:
        selected = filedialog.askdirectory(title="Chọn thư mục output session")
        if selected:
            self.output_root_var.set(selected)

    def _open_bulk_import_dialog(self) -> None:
        if self.running:
            return
        if self.bulk_import_window is not None and self.bulk_import_window.winfo_exists():
            self.bulk_import_window.lift()
            self.bulk_import_window.focus_force()
            return
        dialog = tk.Toplevel(self.root)
        self.bulk_import_window = dialog
        dialog.title("Nh\u1eadp h\u00e0ng lo\u1ea1t")
        dialog.geometry("860x680")
        dialog.minsize(760, 560)
        dialog.transient(self.root)
        dialog.configure(bg=PALETTE["card"])
        dialog.protocol("WM_DELETE_WINDOW", self._close_bulk_import_dialog)

        container = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Nh\u1eadp h\u00e0ng lo\u1ea1t video v\u00e0 \u1ea3nh theo th\u1ee9 t\u1ef1", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            container,
            text="D\u00e1n m\u1ed7i link video tr\u00ean m\u1ed9t d\u00f2ng. \u1ede d\u01b0\u1edbi ch\u1ecdn folder \u1ea3nh, app s\u1ebd gh\u00e9p \u1ea3nh th\u1ee9 1 cho video th\u1ee9 1, \u1ea3nh th\u1ee9 2 cho video th\u1ee9 2, v\u00e0 ti\u1ebfp t\u1ee5c theo th\u1ee9 t\u1ef1.",
            style="Body.TLabel",
            wraplength=780,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        editor_box = ttk.Frame(container, style="Card.TFrame")
        editor_box.pack(fill="both", expand=True, pady=(16, 0))
        line_numbers = tk.Text(
            editor_box,
            width=4,
            wrap="none",
            state="disabled",
            takefocus=0,
            relief="flat",
            bg=PALETTE["log_bg"],
            fg=PALETTE["muted"],
            font=("Consolas", 10),
            padx=8,
            pady=12,
        )
        line_numbers.pack(side="left", fill="y")
        url_text = tk.Text(
            editor_box,
            wrap="none",
            relief="flat",
            bg=PALETTE["input_bg"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            font=("Consolas", 10),
            padx=12,
            pady=12,
        )
        url_text.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(editor_box, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        scrollbar.configure(command=lambda *args: self._bulk_scroll_views(url_text, line_numbers, *args))
        url_text.configure(
            yscrollcommand=lambda first, last: self._bulk_yscroll_callback(
                first,
                last,
                scrollbar,
                line_numbers,
            )
        )
        url_text.bind("<<Modified>>", lambda event: self._on_bulk_editor_modified(url_text, line_numbers))
        url_text.bind("<KeyRelease>", lambda event: self._refresh_bulk_line_numbers(url_text, line_numbers))
        url_text.bind("<MouseWheel>", lambda event: self.root.after_idle(lambda: self._refresh_bulk_line_numbers(url_text, line_numbers)))
        url_text.bind("<ButtonRelease-1>", lambda event: self.root.after_idle(lambda: self._refresh_bulk_line_numbers(url_text, line_numbers)))

        existing_urls = self._existing_row_urls_text()
        if existing_urls:
            url_text.insert("1.0", existing_urls)
        folder_var = tk.StringVar(value=self._guess_existing_image_folder())
        self._refresh_bulk_line_numbers(url_text, line_numbers)
        url_text.edit_modified(False)

        folder_box = ttk.Frame(container, style="Card.TFrame")
        folder_box.pack(fill="x", pady=(16, 0))
        folder_box.columnconfigure(0, weight=1)
        ttk.Label(folder_box, text="Folder \u1ea3nh theo th\u1ee9 t\u1ef1", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(folder_box, textvariable=folder_var).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(
            folder_box,
            text="Ch\u1ecdn folder \u1ea3nh",
            style="Secondary.TButton",
            command=lambda: self._browse_bulk_image_folder(folder_var),
        ).grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(8, 0))

        action_row = ttk.Frame(container, style="Card.TFrame")
        action_row.pack(fill="x", pady=(18, 0))
        action_row.columnconfigure(0, weight=1)
        ttk.Button(action_row, text="\u0110\u00f3ng", style="Ghost.TButton", command=self._close_bulk_import_dialog).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_row,
            text="\u00c1p d\u1ee5ng v\u00e0o danh s\u00e1ch",
            style="Primary.TButton",
            command=lambda: self._submit_bulk_import(url_text, folder_var),
        ).grid(row=0, column=1, sticky="e")

        dialog.grab_set()
        url_text.focus_set()

    def _close_bulk_import_dialog(self) -> None:
        if self.bulk_import_window is None:
            return
        if self.bulk_import_window.winfo_exists():
            self.bulk_import_window.destroy()
        self.bulk_import_window = None

    def _browse_bulk_image_folder(self, folder_var: tk.StringVar) -> None:
        selected = filedialog.askdirectory(title="Ch\u1ecdn folder \u1ea3nh cho nh\u1eadp h\u00e0ng lo\u1ea1t")
        if selected:
            folder_var.set(selected)

    def _bulk_scroll_views(self, url_text: tk.Text, line_numbers: tk.Text, *args) -> None:
        url_text.yview(*args)
        line_numbers.yview(*args)

    def _bulk_yscroll_callback(self, first: str, last: str, scrollbar: ttk.Scrollbar, line_numbers: tk.Text) -> None:
        scrollbar.set(first, last)
        line_numbers.yview_moveto(first)

    def _on_bulk_editor_modified(self, url_text: tk.Text, line_numbers: tk.Text) -> None:
        self._refresh_bulk_line_numbers(url_text, line_numbers)
        url_text.edit_modified(False)

    def _refresh_bulk_line_numbers(self, url_text: tk.Text, line_numbers: tk.Text) -> None:
        line_count = max(1, int(url_text.index("end-1c").split(".")[0]))
        content = "\n".join(str(index) for index in range(1, line_count + 1))
        line_numbers.configure(state="normal")
        line_numbers.delete("1.0", "end")
        line_numbers.insert("1.0", content)
        line_numbers.configure(state="disabled")
        line_numbers.yview_moveto(url_text.yview()[0])

    def _existing_row_urls_text(self) -> str:
        return "\n".join(row.url_var.get().strip() for row in self.rows if row.url_var.get().strip())

    def _guess_existing_image_folder(self) -> str:
        folders = {
            str(Path(row.image_var.get().strip()).expanduser().resolve().parent)
            for row in self.rows
            if row.image_var.get().strip()
        }
        return folders.pop() if len(folders) == 1 else ""

    def _submit_bulk_import(self, url_text: tk.Text, folder_var: tk.StringVar) -> None:
        try:
            bulk_items = self._prepare_bulk_import_items(url_text.get("1.0", "end-1c"), folder_var.get())
        except ValueError as exc:
            messagebox.showerror("Kh\u00f4ng th\u1ec3 nh\u1eadp h\u00e0ng lo\u1ea1t", str(exc))
            return
        self._apply_bulk_import_items(bulk_items)
        self._close_bulk_import_dialog()

    def _prepare_bulk_import_items(self, raw_text: str, folder_path_text: str) -> List[tuple[str, Path]]:
        video_urls = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not video_urls:
            raise ValueError("H\u00e3y nh\u1eadp \u00edt nh\u1ea5t 1 link video, m\u1ed7i d\u00f2ng 1 link.")
        if len(video_urls) > self.config.max_session_items:
            raise ValueError("S\u1ed1 link v\u01b0\u1ee3t qu\u00e1 gi\u1edbi h\u1ea1n %d item c\u1ee7a session." % self.config.max_session_items)
        if not folder_path_text or not folder_path_text.strip():
            raise ValueError("H\u00e3y ch\u1ecdn folder \u1ea3nh tr\u01b0\u1edbc khi \u00e1p d\u1ee5ng.")
        folder_path = Path(folder_path_text).expanduser().resolve()
        if not folder_path.exists() or not folder_path.is_dir():
            raise ValueError("Folder \u1ea3nh kh\u00f4ng t\u1ed3n t\u1ea1i ho\u1eb7c kh\u00f4ng h\u1ee3p l\u1ec7.")
        image_paths = self._list_bulk_image_paths(folder_path)
        if not image_paths:
            raise ValueError("Folder \u1ea3nh kh\u00f4ng c\u00f3 file PNG, JPG ho\u1eb7c JPEG.")
        if len(image_paths) != len(video_urls):
            raise ValueError(
                "S\u1ed1 link video (%d) v\u00e0 s\u1ed1 \u1ea3nh trong folder (%d) ph\u1ea3i b\u1eb1ng nhau."
                % (len(video_urls), len(image_paths))
            )
        return list(zip(video_urls, image_paths))

    def _list_bulk_image_paths(self, folder_path: Path) -> List[Path]:
        image_paths = [
            path
            for path in folder_path.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_BULK_IMAGE_SUFFIXES
        ]
        return sorted(image_paths, key=self._bulk_image_sort_key)

    def _bulk_image_sort_key(self, image_path: Path):
        parts = re.split(r"(\d+)", image_path.stem.lower())
        key = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        key.append((1, image_path.suffix.lower()))
        key.append((1, image_path.name.lower()))
        return tuple(key)

    def _apply_bulk_import_items(self, bulk_items: List[tuple[str, Path]]) -> None:
        self.latest_result = None
        self.current_session_dir = None
        self.review_ready = False
        self.summary_path_var.set("Ch\u01b0a c\u00f3 output session.")
        self.open_session_button.configure(state="disabled")
        self.finalize_button.configure(state="disabled")
        self._ensure_row_count(len(bulk_items))
        for index, (video_url, image_path) in enumerate(bulk_items):
            row = self.rows[index]
            row.url_var.set(video_url)
            row.image_var.set(str(image_path))
            row.output_dir = None
            row.preview_video_path = None
            row.open_button.configure(state="disabled")
            self._set_row_status(row, "draft", "\u0110\u00e3 n\u1ea1p t\u1eeb ch\u1ebf \u0111\u1ed9 nh\u1eadp h\u00e0ng lo\u1ea1t.")
        self._set_session_status("draft", "\u0110\u00e3 n\u1ea1p %d item t\u1eeb ch\u1ebf \u0111\u1ed9 nh\u1eadp h\u00e0ng lo\u1ea1t." % len(bulk_items))
        self.summary_counts_var.set("%d item | 0 ho\u00e0n t\u1ea5t | 0 l\u1ed7i" % len(bulk_items))
        self._append_log("\u0110\u00e3 n\u1ea1p %d item t\u1eeb nh\u1eadp h\u00e0ng lo\u1ea1t." % len(bulk_items), reset=True)

    def _ensure_row_count(self, target_count: int) -> None:
        while len(self.rows) < target_count:
            self._add_row()
        while len(self.rows) > target_count:
            row = self.rows.pop()
            row.frame.destroy()
        self._renumber_rows()

    def _start_session(self) -> None:
        if self.running:
            return
        self.latest_result = None
        self.session_rerun_queue = queue.Queue()
        self.current_session_dir = None
        self.summary_path_var.set("Đang chuẩn bị session...")
        self.open_session_button.configure(state="disabled")
        session_spec = self._build_session_spec()
        self._clear_all_row_states_for_run()
        self._set_running_state(True)
        self._set_session_status("validating_session", "Đang kiểm tra toàn bộ danh sách trước khi chạy.")
        threading.Thread(target=self._run_session_worker, args=(session_spec,), daemon=True).start()

    def _build_session_spec(self) -> SessionSpec:
        items = []
        for row in self.rows:
            image_text = row.image_var.get().strip()
            items.append(SessionItemSpec(row_id=row.row_id, source_video_url=row.url_var.get().strip(), product_image=Path(image_text) if image_text else None, overlay_alpha_ratio=self._fade_ratio_from_blur_percent(row.opacity_var.get())))
        return SessionSpec(items=items, output_root_dir=Path(self.output_root_var.get().strip() or str(self.config.default_output_root)), session_name=(self.session_name_var.get().strip() or None), cookies_file=None)

    def _run_session_worker(self, session_spec: SessionSpec) -> None:
        result = self.orchestrator.run(
            session_spec,
            event_callback=self._queue_event,
            rerun_queue=self.session_rerun_queue,
        )
        self._queue_event(SessionEvent(event_type="worker_result", payload={"result": result}))

    def _approve_session_outputs(self) -> None:
        if self.running or self.latest_result is None:
            return
        self._set_running_state(True)
        self._set_session_status("running", "Đang lưu toàn bộ video đã duyệt vào output cuối.")
        threading.Thread(target=self._finalize_session_worker, daemon=True).start()

    def _send_session_via_telegram(self) -> None:
        runtime_config = self._runtime_telegram_config()
        if not runtime_config.allow_local_telegram:
            messagebox.showinfo("Chưa cấu hình Telegram", "Nhập bot token và chat ID của riêng bạn rồi bấm Lưu cấu hình Telegram trước khi gửi.")
            return
        if self.running or self.latest_result is None or not self.review_ready:
            return
        chat_id = self._normalize_telegram_chat_id(self.telegram_chat_id_var.get())
        if chat_id is None:
            messagebox.showerror("Thiếu Telegram Chat ID", "Nhập Telegram Chat ID hợp lệ trước khi gửi.")
            return
        self._set_running_state(True)
        self._set_session_status("running", "Đang gửi các video đã duyệt qua Telegram.")
        threading.Thread(target=self._send_session_via_telegram_worker, args=(chat_id, runtime_config), daemon=True).start()

    def _send_session_via_telegram_worker(self, chat_id: int, runtime_config: PipelineConfig) -> None:
        try:
            self.telegram_delivery = TelegramDeliveryService(runtime_config, logger=self.logger)
            payload = self.telegram_delivery.send_session_result(self.latest_result, chat_id)
            self._queue_event(SessionEvent(event_type="telegram_delivery_result", payload=payload))
        except Exception as exc:
            self._queue_event(SessionEvent(event_type="telegram_delivery_failed", message=str(exc)))

    def _finalize_session_worker(self) -> None:
        try:
            result = self.orchestrator.finalize_reviewed_session(self.latest_result, event_callback=self._queue_event)
            self._queue_event(SessionEvent(event_type="review_finalize_result", payload={"result": result}))
        except Exception as exc:
            self._queue_event(SessionEvent(event_type="review_finalize_failed", message=str(exc)))

    def _rerun_row(self, row_id: str) -> None:
        row = self._find_row_by_id(row_id)
        if row is None:
            return
        row_index = self.rows.index(row)
        image_text = row.image_var.get().strip()
        item_spec = SessionItemSpec(
            row_id=row.row_id,
            source_video_url=row.url_var.get().strip(),
            product_image=Path(image_text) if image_text else None,
            overlay_alpha_ratio=self._fade_ratio_from_blur_percent(row.opacity_var.get()),
        )
        if self.running:
            row.rerun_button.configure(state="disabled")
            self._set_row_status(row, "processing", "\u0110ang x\u1ebfp y\u00eau c\u1ea7u t\u1ea1o l\u1ea1i cho item n\u00e0y.")
            self._set_session_status("running", "\u0110ang t\u1ea1o l\u1ea1i item %d trong khi c\u00e1c item kh\u00e1c v\u1eabn ti\u1ebfp t\u1ee5c ch\u1ea1y." % (row_index + 1))
            self._append_log("Queued rerun for item %d while session is still running." % (row_index + 1))
            self.session_rerun_queue.put((row_index, item_spec))
            return
        if self.latest_result is None:
            return
        self.review_ready = False
        self._set_running_state(True)
        self._set_row_status(row, "processing", "Đang tạo lại video preview cho item này.")
        self._set_session_status("running", "Đang tạo lại item %d để bạn xem lại." % (row_index + 1))
        threading.Thread(target=self._rerun_row_worker, args=(row_index, item_spec), daemon=True).start()

    def _rerun_row_worker(self, row_index: int, item_spec: SessionItemSpec) -> None:
        try:
            result = self.orchestrator.rerun_item_for_review(
                self.latest_result,
                item_spec,
                row_index,
                event_callback=self._queue_event,
            )
            self._queue_event(SessionEvent(event_type="review_rerun_result", item_index=row_index, payload={"result": result}))
        except Exception as exc:
            self._queue_event(SessionEvent(event_type="review_rerun_failed", item_index=row_index, message=str(exc)))

    def _clear_all_row_states_for_run(self) -> None:
        self.review_ready = False
        self.finalize_button.configure(state="disabled")
        for row in self.rows:
            row.output_dir = None
            row.preview_video_path = None
            row.open_button.configure(state="disabled")
            row.rerun_button.configure(state="disabled")
            self._set_row_status(row, "queued", "Đang chờ trong hàng đợi.")
        self.summary_counts_var.set("%d item | 0 hoàn tất | 0 lỗi" % len(self.rows))
        self._append_log("Session queued with %d item(s)." % len(self.rows), reset=True)

    def _set_running_state(self, is_running: bool) -> None:
        self.running = is_running
        state = "disabled" if is_running else "normal"
        self.add_row_button.configure(state=state)
        self.bulk_import_button.configure(state=state)
        self.run_button.configure(state=state)
        self.output_root_entry.configure(state=state)
        self.output_browse_button.configure(state=state)
        self.session_name_entry.configure(state=state)
        if self.telegram_token_entry is not None:
            self.telegram_token_entry.configure(state=state)
        if self.telegram_chat_id_entry is not None:
            self.telegram_chat_id_entry.configure(state=state)
        for row in self.rows:
            row.url_entry.configure(state=state)
            row.image_entry.configure(state=state)
            row.opacity_scale.configure(state=state)
            row.browse_button.configure(state=state)
            row.remove_button.configure(state=state)
            row.rerun_button.configure(state=self._rerun_button_state(row, is_running))
        if is_running:
            self.finalize_button.configure(state="disabled")
        else:
            self.finalize_button.configure(state="normal" if self.review_ready else "disabled")

    def _rerun_button_state(self, row: SessionRowWidgets, is_running: Optional[bool] = None) -> str:
        current_running = self.running if is_running is None else is_running
        if current_running:
            return "normal" if row.status_var.get() == STATUS_LABELS["failed"] else "disabled"
        return (
            "normal"
            if row.status_var.get() in (STATUS_LABELS["completed"], STATUS_LABELS["failed"])
            else "disabled"
        )

    def _queue_event(self, event: SessionEvent) -> None:
        self.event_queue.put(event)

    def _poll_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.root.after(self.config.ui_poll_interval_ms, self._poll_events)

    def _poll_license_heartbeat(self) -> None:
        if self.license_guard is None:
            return
        if self.license_reauthentication_required:
            return
        try:
            session = self.license_guard.heartbeat()
            self.license_session = session
            self.license_summary_var.set(self._license_summary_text())
        except Exception as exc:
            self.logger.exception("License heartbeat failed.")
            self._handle_runtime_license_invalid(str(exc))
            return
        self.root.after(
            max(30, int(self.license_guard.config.heartbeat_interval_seconds)) * 1000,
            self._poll_license_heartbeat,
        )

    def _handle_event(self, event: SessionEvent) -> None:
        if event.event_type == "session_started":
            self._set_session_status(event.status or "draft", event.message or "Session đã được tạo.")
            self._append_log(event.message or "Session created.")
            return
        if event.event_type == "session_stage":
            self._set_session_status(event.status or "running", event.message or "")
            self._append_log(event.message or "")
            return
        if event.event_type == "session_validation_failed":
            self._set_session_status("failed_session", event.message or "Session validation failed.")
            self._apply_row_errors(event.payload.get("row_errors") or {})
            self._append_log(event.message or "Session validation failed.")
            return
        if event.event_type == "item_started":
            row = self._find_row_by_index(event.item_index)
            if row:
                self._set_row_status(row, event.status or "queued", event.message or "Đang chờ xử lý.")
            return
        if event.event_type == "item_stage":
            row = self._find_row_by_index(event.item_index)
            if row:
                self._set_row_status(row, event.status or "processing", STAGE_LABELS.get(event.stage or "", event.message or "Đang xử lý."))
            return
        if event.event_type == "item_warning":
            self._append_log("Item %d warning: %s" % (((event.item_index or 0) + 1), event.message or ""))
            return
        if event.event_type == "item_completed":
            row = self._find_row_by_index(event.item_index)
            if row:
                row.output_dir = event.payload.get("output_dir")
                row.preview_video_path = event.payload.get("final_video_path")
                row.open_button.configure(state="normal" if row.preview_video_path else "disabled")
                row.rerun_button.configure(state=self._rerun_button_state(row))
                self._set_row_status(row, "completed", "Đã tạo xong video preview cho item này.")
            self._append_log("Item %d completed." % ((event.item_index or 0) + 1))
            return
        if event.event_type == "item_failed":
            row = self._find_row_by_index(event.item_index)
            if row:
                row.output_dir = event.payload.get("output_dir")
                row.preview_video_path = None
                row.open_button.configure(state="disabled")
                row.rerun_button.configure(state=self._rerun_button_state(row))
                self._set_row_status(row, "failed", event.message or "Item thất bại.")
            self._append_log("Item %d failed: %s" % (((event.item_index or 0) + 1), event.message or ""))
            return
        if event.event_type == "session_progress":
            self.summary_counts_var.set("%d item | %d hoàn tất | %d lỗi" % (event.payload.get("total_items", len(self.rows)), event.payload.get("completed_items", 0), event.payload.get("failed_items", 0)))
            return
        if event.event_type == "session_completed":
            payload = event.payload or {}
            self._set_session_status(event.status or "completed_with_success", event.message or "Session đã hoàn tất.")
            self.summary_counts_var.set("%d item | %d hoàn tất | %d lỗi" % (payload.get("item_count_total", len(self.rows)), payload.get("item_count_completed", 0), payload.get("item_count_failed", 0)))
            self._append_log("Session completed.")
            return
        if event.event_type == "session_failed":
            if self._should_reauthenticate_from_message(event.message):
                self._append_log("Session yêu cầu đăng nhập lại: %s" % (event.message or ""))
                self._handle_runtime_license_invalid(event.message or "Phiên đăng nhập hiện tại không còn hợp lệ.")
                return
            self._set_session_status("failed_session", event.message or "Session thất bại.")
            self._append_log("Session failed: %s" % (event.message or ""))
            return
        if event.event_type == "session_finalized":
            self._append_log(event.message or "Session outputs were approved and saved.")
            return
        if event.event_type == "worker_result":
            self._finalize_result(event.payload.get("result"))
            return
        if event.event_type == "review_rerun_result":
            self._finalize_result(event.payload.get("result"), message="Đã tạo lại xong item, bạn có thể xem và duyệt tiếp.")
            return
        if event.event_type == "review_rerun_failed":
            self._set_running_state(False)
            self._set_session_status("completed_with_partial_failure", event.message or "Không thể tạo lại item.")
            self._append_log("Rerun failed: %s" % (event.message or ""))
            messagebox.showerror("Không thể tạo lại", event.message or "Không thể tạo lại item.")
            return
        if event.event_type == "review_finalize_result":
            self._finalize_result(event.payload.get("result"), message="Session đã được duyệt và lưu vào output cuối.")
            return
        if event.event_type == "review_finalize_failed":
            self._set_running_state(False)
            self.review_ready = True
            self.finalize_button.configure(state="normal")
            self._append_log("Finalize failed: %s" % (event.message or ""))
            messagebox.showerror("Không thể lưu output", event.message or "Không thể lưu session.")
            return

        if event.event_type == "telegram_delivery_result":
            payload = event.payload or {}
            self._set_running_state(False)
            self.review_ready = any(item.status == "completed" for item in (self.latest_result.items if self.latest_result else []))
            self.finalize_button.configure(state="normal" if self.review_ready else "disabled")
            self._set_session_status("completed_with_success", "Đã gửi video qua Telegram thành công.")
            self._append_log("Telegram delivery completed: sent %s video(s)." % payload.get("sent_count", 0))
            messagebox.showinfo(
                "Đã gửi qua Telegram",
                "Đã gửi %s video sang chat %s." % (payload.get("sent_count", 0), payload.get("chat_id", "")),
            )
            return
        if event.event_type == "telegram_delivery_failed":
            self._set_running_state(False)
            self.review_ready = True
            self.finalize_button.configure(state="normal")
            self._append_log("Telegram delivery failed: %s" % (event.message or ""))
            self._set_session_status("completed_with_partial_failure", event.message or "Không thể gửi session qua Telegram.")
            messagebox.showerror("Không thể gửi Telegram", event.message or "Không thể gửi session qua Telegram.")
            return

    def _finalize_result(self, result: Optional[SessionResult], message: Optional[str] = None) -> None:
        self.latest_result = result
        self._set_running_state(False)
        if result is None:
            self._set_session_status("failed_session", "Không nhận được kết quả session.")
            messagebox.showerror("Session thất bại", "Không nhận được kết quả session.")
            return
        if result.row_errors:
            self._apply_row_errors(result.row_errors)
            messagebox.showwarning("Kiểm tra lại dữ liệu", "Session chưa thể chạy vì còn dòng invalid. Xem trạng thái từng dòng để sửa.")
            return
        if result.artifacts and result.artifacts.session_dir:
            self.current_session_dir = str(result.artifacts.session_dir)
            self.summary_path_var.set(str(result.artifacts.session_dir))
            self.open_session_button.configure(state="normal")
            self._sync_rows_from_result(result)
        else:
            self.current_session_dir = None
            self.summary_path_var.set("Session không tạo được thư mục đầu ra.")
        if result.artifacts and result.artifacts.is_finalized:
            self.review_ready = False
            self.finalize_button.configure(state="disabled")
            self._set_session_status(result.status, message or "Session đã được lưu vào output cuối.")
            messagebox.showinfo("Đã lưu output", "Toàn bộ video đã được lưu vào output cuối.")
            return
        self.review_ready = any(item.status == "completed" for item in result.items)
        self.finalize_button.configure(state="normal" if self.review_ready else "disabled")
        if result.status == "completed_with_success":
            self._set_session_status(result.status, message or "Tất cả video preview đã xong. Xem lại từng item, tạo lại nếu cần, rồi bấm OK để lưu.")
            if message is None:
                messagebox.showinfo("Session sẵn sàng duyệt", "Xem lại từng video preview, nếu ổn thì bấm OK để lưu toàn bộ vào output.")
        elif result.status == "completed_with_partial_failure":
            self._set_session_status(result.status, message or "Có item lỗi hoặc chưa ưng ý. Bạn có thể tạo lại các item đã xong trước khi lưu.")
            if message is None:
                messagebox.showwarning("Session cần duyệt lại", "Có item lỗi hoặc cần xem lại. Bạn có thể tạo lại từng item rồi bấm OK để lưu.")
        elif result.status == "failed_session":
            messagebox.showerror("Session thất bại", "Session không hoàn tất được. Xem nhật ký để biết thêm chi tiết.")

    def _apply_row_errors(self, row_errors: Dict[int, List[str]]) -> None:
        for index, messages in row_errors.items():
            row = self._find_row_by_index(index)
            if row:
                self._set_row_status(row, "invalid", " | ".join(messages))
        for index, row in enumerate(self.rows):
            if index not in row_errors and row.status_var.get() != STATUS_LABELS["completed"]:
                self._set_row_status(row, "draft", "Sẵn sàng sau khi sửa các dòng lỗi khác.")

    def _set_row_status(self, row: SessionRowWidgets, status: str, detail: str) -> None:
        row.status_var.set(STATUS_LABELS.get(status, status))
        row.detail_var.set(detail)
        row.status_chip.configure(text=row.status_var.get())
        self._set_chip_style(row.status_chip, status)
        row.rerun_button.configure(state=self._rerun_button_state(row))

    def _set_session_status(self, status: str, detail: str) -> None:
        self.session_status_var.set(STATUS_LABELS.get(status, status))
        self.session_detail_var.set(detail)
        if self.session_chip is not None:
            self.session_chip.configure(text=self.session_status_var.get())
            self._set_chip_style(self.session_chip, status)

    def _set_chip_style(self, widget: tk.Label, status: str) -> None:
        bg, fg = STATUS_COLORS.get(status, STATUS_COLORS["draft"])
        widget.configure(bg=bg, fg=fg)

    def _append_log(self, message: str, reset: bool = False) -> None:
        if not message:
            return
        self.log_text.configure(state="normal")
        if reset:
            self.log_text.delete("1.0", "end")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _sync_rows_from_result(self, result: SessionResult) -> None:
        items_by_index = {item.item_index: item for item in result.items}
        for index, row in enumerate(self.rows):
            item = items_by_index.get(index)
            if item is None:
                row.output_dir = None
                row.preview_video_path = None
                row.open_button.configure(state="disabled")
                row.rerun_button.configure(state="disabled")
                continue
            row.output_dir = str(item.output_dir) if item.output_dir else None
            row.preview_video_path = str(item.artifacts.final_video_path) if item.artifacts.final_video_path else None
            row.open_button.configure(state="normal" if row.preview_video_path else "disabled")
            row.rerun_button.configure(
                state="normal"
                if (
                    not self.running
                    and not (result.artifacts and result.artifacts.is_finalized)
                    and item.status in ("completed", "failed")
                )
                else "disabled"
            )

    def _open_row_output(self, row_id: str) -> None:
        row = self._find_row_by_id(row_id)
        if row:
            self._open_path(row.preview_video_path or row.output_dir)

    def _open_path(self, path_value: Optional[str]) -> None:
        if not path_value:
            return
        try:
            os.startfile(path_value)  # type: ignore[attr-defined]
        except Exception as exc:
            self.logger.exception("Unable to open path %s", path_value)
            messagebox.showerror("Không thể mở thư mục", str(exc))

    def _normalize_telegram_chat_id(self, value: str) -> Optional[int]:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            return int(normalized)
        except ValueError:
            return None

    def _find_row_by_id(self, row_id: str) -> Optional[SessionRowWidgets]:
        for row in self.rows:
            if row.row_id == row_id:
                return row
        return None

    def _find_row_by_index(self, index: Optional[int]) -> Optional[SessionRowWidgets]:
        if index is None:
            return None
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def _bind_rows_mousewheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self._on_rows_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_rows_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_rows_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_rows_mousewheel(child)

    def _on_rows_mousewheel(self, event: tk.Event) -> str:
        canvas = getattr(self, "rows_canvas", None)
        if canvas is None:
            return "break"
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            raw_delta = getattr(event, "delta", 0)
            if raw_delta == 0:
                return "break"
            delta = -1 * int(raw_delta / 120) if abs(raw_delta) >= 120 else (-1 if raw_delta > 0 else 1)
        canvas.yview_scroll(delta, "units")
        return "break"

    def _bind_summary_mousewheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self._on_summary_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_summary_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_summary_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_summary_mousewheel(child)

    def _on_summary_mousewheel(self, event: tk.Event) -> str:
        canvas = getattr(self, "summary_canvas", None)
        if canvas is None:
            return "break"
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            raw_delta = getattr(event, "delta", 0)
            if raw_delta == 0:
                return "break"
            delta = -1 * int(raw_delta / 120) if abs(raw_delta) >= 120 else (-1 if raw_delta > 0 else 1)
        canvas.yview_scroll(delta, "units")
        return "break"

    def _logout_and_relogin(self) -> None:
        if self.license_guard is not None:
            try:
                self.license_guard.logout()
            except Exception:
                self.logger.exception("Logout failed.")
        self._stop_embedded_telegram_bot()
        self.reauthenticate_requested = True
        self.root.destroy()

    def _handle_runtime_license_invalid(self, reason: str) -> None:
        if self.license_reauthentication_required:
            return
        self.license_reauthentication_required = True
        self.license_warning_message = reason
        self.running = False
        self._stop_embedded_telegram_bot()
        self._set_session_status("failed_session", reason)
        self._append_log("License/runtime check failed: %s" % reason)
        self.run_button.configure(state="disabled")
        self.finalize_button.configure(state="disabled")
        self.bulk_import_button.configure(state="disabled")
        self.add_row_button.configure(state="disabled")
        self.output_root_entry.configure(state="disabled")
        self.output_browse_button.configure(state="disabled")
        self.session_name_entry.configure(state="disabled")
        if self.telegram_token_entry is not None:
            self.telegram_token_entry.configure(state="disabled")
        if self.telegram_chat_id_entry is not None:
            self.telegram_chat_id_entry.configure(state="disabled")
        for row in self.rows:
            row.url_entry.configure(state="disabled")
            row.image_entry.configure(state="disabled")
            row.opacity_scale.configure(state="disabled")
            row.browse_button.configure(state="disabled")
            row.remove_button.configure(state="disabled")
            row.rerun_button.configure(state="disabled")
        messagebox.showwarning(
            "Phiên đã hết hiệu lực",
            "Tài khoản hiện tại đã bị khóa, hết hạn, hoặc không còn hợp lệ.\n\nChi tiết: %s\n\nBấm 'Đăng xuất' để đăng nhập lại." % reason,
        )

    def _license_runtime_checkpoint(self) -> None:
        if self.license_guard is None:
            return
        self.license_session = self.license_guard.heartbeat()

    def _should_reauthenticate_from_message(self, message: Optional[str]) -> bool:
        normalized = str(message or "").strip().lower()
        if not normalized:
            return False
        triggers = (
            "đăng nhập lại",
            "không còn hợp lệ",
            "invalid access token signature",
            "access token expired",
            "session is no longer active",
            "session expired",
            "license has expired",
        )
        return any(trigger in normalized for trigger in triggers)

    def _redirect_to_login(self, reason: str) -> None:
        self.reauthenticate_requested = True
        self.running = False
        messagebox.showwarning(
            "Cần đăng nhập lại",
            "Phiên sử dụng hiện tại không còn hợp lệ.\n\nChi tiết: %s" % reason,
        )
        self.root.destroy()

    def _license_summary_text(self) -> str:
        if self.license_session is None:
            return "Chua co phien dang nhap."
        license_expires_at = _as_utc(self.license_session.license_expires_at)
        remaining = license_expires_at - datetime.now(timezone.utc)
        remaining_days = max(0, int((remaining.total_seconds() + 86399) // 86400))
        expires_label = license_expires_at.strftime("%d/%m/%Y")
        return "Tài khoản: %s | Gói: %s | Còn %s ngày | Hết hạn: %s" % (
            self.license_session.username,
            self.license_session.plan_name,
            remaining_days,
            expires_label,
        )


def launch_ui(config: Optional[PipelineConfig] = None, license_guard: Optional[LicenseGuard] = None) -> int:
    runtime_config = config or PipelineConfig.from_env()
    ensure_runtime_allowed(runtime_config, surface="ui")
    configure_tk_environment()
    guard = license_guard or LicenseGuard()
    while True:
        session = ensure_ui_license_session(guard, logger=logging.getLogger("auto_tiktok_editor.license_ui"))
        if session is None:
            return 1
        root = tk.Tk()
        app = EditorApplication(root, config=runtime_config, license_guard=guard, license_session=session)
        root.mainloop()
        if getattr(app, "reauthenticate_requested", False) is not True:
            return 0

