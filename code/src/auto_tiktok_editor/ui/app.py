"""Tkinter UI for the session-based Auto TikTok Editor MVP."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionEvent, SessionItemSpec, SessionResult, SessionSpec


STAGE_LABELS = {
    "validating_input": "Đang kiểm tra item",
    "downloading_source": "Đang tải video TikTok",
    "normalizing_media": "Đang chuẩn hóa media",
    "speed_processing": "Đang tăng tốc 1.2x",
    "detecting_scenes": "Đang phát hiện scene",
    "planning_edit": "Đang tạo edit plan",
    "rendering_final": "Đang render video final",
    "exporting_artifacts": "Đang xuất artifact",
}

STATUS_LABELS = {
    "draft": "Nháp",
    "invalid": "Lỗi input",
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
    "completed_with_partial_failure": "Hoàn tất, có item lỗi",
    "failed_session": "Session lỗi",
}

STATUS_COLORS = {
    "draft": ("#E2E8F0", "#334155"),
    "invalid": ("#FEE2E2", "#991B1B"),
    "queued": ("#DBEAFE", "#1D4ED8"),
    "validating": ("#FEF3C7", "#92400E"),
    "downloading": ("#D1FAE5", "#065F46"),
    "processing": ("#DCFCE7", "#166534"),
    "completed": ("#DCFCE7", "#166534"),
    "failed": ("#FEE2E2", "#991B1B"),
    "validating_session": ("#FEF3C7", "#92400E"),
    "ready_to_run": ("#DBEAFE", "#1D4ED8"),
    "running": ("#DBEAFE", "#1D4ED8"),
    "completed_with_success": ("#DCFCE7", "#166534"),
    "completed_with_partial_failure": ("#FEF3C7", "#92400E"),
    "failed_session": ("#FEE2E2", "#991B1B"),
}


@dataclass
class SessionRowWidgets:
    row_id: str
    frame: ttk.Frame
    index_var: tk.StringVar
    url_var: tk.StringVar
    image_var: tk.StringVar
    status_var: tk.StringVar
    detail_var: tk.StringVar
    url_entry: ttk.Entry
    image_entry: ttk.Entry
    browse_button: ttk.Button
    remove_button: ttk.Button
    open_button: ttk.Button
    status_chip: tk.Label
    output_dir: Optional[str] = None


class EditorApplication(object):
    def __init__(
        self,
        root: tk.Tk,
        config: Optional[PipelineConfig] = None,
        orchestrator: Optional[SessionOrchestrator] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.root = root
        self.config = config or PipelineConfig.from_env()
        self.orchestrator = orchestrator or SessionOrchestrator(self.config)
        self.logger = logger or logging.getLogger("auto_tiktok_editor.ui")
        self.event_queue = queue.Queue()
        self.rows = []  # type: List[SessionRowWidgets]
        self.row_counter = 0
        self.running = False
        self.latest_result = None  # type: Optional[SessionResult]
        self.current_session_dir = None  # type: Optional[str]

        self.session_name_var = tk.StringVar()
        self.output_root_var = tk.StringVar(value=str(self.config.default_output_root))
        self.cookies_file_var = tk.StringVar()
        self.session_status_var = tk.StringVar(value=STATUS_LABELS["draft"])
        self.session_detail_var = tk.StringVar(value="Tạo danh sách item rồi bấm chạy session.")
        self.summary_counts_var = tk.StringVar(value="0 item | 0 hoàn tất | 0 lỗi")
        self.summary_path_var = tk.StringVar(value="Chưa có output session.")

        self._configure_window()
        self._build_styles()
        self._build_layout()
        self._add_row()
        self.root.after(self.config.ui_poll_interval_ms, self._poll_events)

    def _configure_window(self) -> None:
        self.root.title("Auto TikTok Video Editor")
        self.root.geometry("1360x880")
        self.root.minsize(1180, 760)
        self.root.configure(bg="#F8FAFC")

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#F8FAFC")
        style.configure("Card.TFrame", background="#FFFFFF", relief="flat")
        style.configure("Card.TLabelframe", background="#FFFFFF", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="#FFFFFF", foreground="#0F172A", font=("Segoe UI", 11, "bold"))
        style.configure("Title.TLabel", background="#F8FAFC", foreground="#0F172A", font=("Segoe UI Semibold", 19, "bold"))
        style.configure("Subtitle.TLabel", background="#F8FAFC", foreground="#475569", font=("Segoe UI", 10))
        style.configure("Field.TLabel", background="#FFFFFF", foreground="#334155", font=("Segoe UI", 10, "bold"))
        style.configure("Body.TLabel", background="#FFFFFF", foreground="#475569", font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10, "bold"), padding=(14, 8))
        style.configure("Secondary.TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("TEntry", padding=6)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="Auto TikTok Video Editor", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Nhập nhiều cặp link TikTok và ảnh sản phẩm, rồi chạy session tuần tự với trạng thái rõ cho từng item.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        controls = ttk.LabelFrame(outer, text="Session Control", style="Card.TLabelframe", padding=16)
        controls.pack(fill="x", pady=(18, 14))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(4, weight=1)

        ttk.Label(controls, text="Tên session", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        self.session_name_entry = ttk.Entry(controls, textvariable=self.session_name_var)
        self.session_name_entry.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(6, 0))

        ttk.Label(controls, text="Thư mục output", style="Field.TLabel").grid(row=0, column=1, columnspan=4, sticky="w")
        self.output_root_entry = ttk.Entry(controls, textvariable=self.output_root_var)
        self.output_root_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(6, 0))
        self.output_browse_button = ttk.Button(
            controls,
            text="Chọn thư mục",
            style="Secondary.TButton",
            command=self._browse_output_root,
        )
        self.output_browse_button.grid(row=1, column=4, sticky="e", padx=(12, 0), pady=(6, 0))

        ttk.Label(controls, text="Cookies.txt (optional)", style="Field.TLabel").grid(row=2, column=0, columnspan=5, sticky="w", pady=(14, 0))
        self.cookies_file_entry = ttk.Entry(controls, textvariable=self.cookies_file_var)
        self.cookies_file_entry.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.cookies_browse_button = ttk.Button(
            controls,
            text="Ch?n cookies.txt",
            style="Secondary.TButton",
            command=self._browse_cookies_file,
        )
        self.cookies_browse_button.grid(row=3, column=4, sticky="e", padx=(12, 0), pady=(6, 0))

        actions = ttk.Frame(controls, style="Card.TFrame")
        actions.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(16, 0))
        actions.columnconfigure(0, weight=1)
        self.add_row_button = ttk.Button(actions, text="+ Thêm dòng", style="Secondary.TButton", command=self._add_row)
        self.add_row_button.grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(actions, text="Chạy session", style="Primary.TButton", command=self._start_session)
        self.run_button.grid(row=0, column=1, sticky="e")

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        left_card = ttk.LabelFrame(body, text="Danh sách item", style="Card.TLabelframe", padding=12)
        right_card = ttk.LabelFrame(body, text="Session Summary", style="Card.TLabelframe", padding=12)
        body.add(left_card, weight=3)
        body.add(right_card, weight=2)

        self._build_rows_panel(left_card)
        self._build_summary_panel(right_card)

    def _build_rows_panel(self, parent: ttk.LabelFrame) -> None:
        container = ttk.Frame(parent, style="Card.TFrame")
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, background="#FFFFFF", highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.rows_host = ttk.Frame(canvas, style="Card.TFrame")
        self.rows_host.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        self.rows_window_id = canvas.create_window((0, 0), window=self.rows_host, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(self.rows_window_id, width=event.width),
        )

    def _build_summary_panel(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(0, weight=1)
        status_frame = ttk.Frame(parent, style="Card.TFrame")
        status_frame.pack(fill="x")
        ttk.Label(status_frame, text="Trạng thái session", style="Field.TLabel").pack(anchor="w")
        self.session_chip = tk.Label(status_frame, text=STATUS_LABELS["draft"], font=("Segoe UI", 9, "bold"), padx=10, pady=5)
        self.session_chip.pack(anchor="w", pady=(8, 0))
        self._set_chip_style(self.session_chip, "draft")
        ttk.Label(status_frame, textvariable=self.session_detail_var, style="Body.TLabel", wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))

        counts_frame = ttk.Frame(parent, style="Card.TFrame")
        counts_frame.pack(fill="x", pady=(18, 0))
        ttk.Label(counts_frame, text="Tổng quan", style="Field.TLabel").pack(anchor="w")
        ttk.Label(counts_frame, textvariable=self.summary_counts_var, style="Body.TLabel").pack(anchor="w", pady=(8, 0))

        output_frame = ttk.Frame(parent, style="Card.TFrame")
        output_frame.pack(fill="x", pady=(18, 0))
        ttk.Label(output_frame, text="Output session", style="Field.TLabel").pack(anchor="w")
        ttk.Label(output_frame, textvariable=self.summary_path_var, style="Body.TLabel", wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))
        self.open_session_button = ttk.Button(
            output_frame,
            text="Mở thư mục session",
            style="Secondary.TButton",
            command=lambda: self._open_path(self.current_session_dir),
            state="disabled",
        )
        self.open_session_button.pack(anchor="w", pady=(10, 0))

        log_frame = ttk.Frame(parent, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True, pady=(18, 0))
        ttk.Label(log_frame, text="Activity Log", style="Field.TLabel").pack(anchor="w")
        self.log_text = tk.Text(
            log_frame,
            height=18,
            wrap="word",
            relief="flat",
            bg="#F8FAFC",
            fg="#0F172A",
            insertbackground="#0F172A",
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        self.log_text.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text.configure(state="disabled")

    def _add_row(self) -> None:
        self.row_counter += 1
        row_id = "row_%03d" % self.row_counter
        row_frame = ttk.Frame(self.rows_host, style="Card.TFrame", padding=14)
        row_frame.pack(fill="x", pady=(0, 12))
        row_frame.columnconfigure(1, weight=1)

        index_var = tk.StringVar(value="Item %d" % len(self.rows))
        url_var = tk.StringVar()
        image_var = tk.StringVar()
        status_var = tk.StringVar(value=STATUS_LABELS["draft"])
        detail_var = tk.StringVar(value="Chưa chạy.")

        header = ttk.Frame(row_frame, style="Card.TFrame")
        header.grid(row=0, column=0, columnspan=4, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=index_var, style="Field.TLabel").grid(row=0, column=0, sticky="w")
        open_button = ttk.Button(
            header,
            text="Mở output",
            style="Secondary.TButton",
            state="disabled",
            command=lambda rid=row_id: self._open_row_output(rid),
        )
        open_button.grid(row=0, column=1, sticky="e", padx=(8, 8))
        remove_button = ttk.Button(
            header,
            text="Xóa dòng",
            style="Secondary.TButton",
            command=lambda rid=row_id: self._remove_row(rid),
        )
        remove_button.grid(row=0, column=2, sticky="e")

        ttk.Label(row_frame, text="Link TikTok public", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 4))
        url_entry = ttk.Entry(row_frame, textvariable=url_var)
        url_entry.grid(row=2, column=0, columnspan=4, sticky="ew")

        ttk.Label(row_frame, text="Ảnh sản phẩm", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 4))
        image_entry = ttk.Entry(row_frame, textvariable=image_var)
        image_entry.grid(row=4, column=0, columnspan=3, sticky="ew")
        browse_button = ttk.Button(
            row_frame,
            text="Chọn ảnh",
            style="Secondary.TButton",
            command=lambda rid=row_id: self._browse_image(rid),
        )
        browse_button.grid(row=4, column=3, sticky="e", padx=(10, 0))

        footer = ttk.Frame(row_frame, style="Card.TFrame")
        footer.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        footer.columnconfigure(1, weight=1)
        status_chip = tk.Label(footer, text=STATUS_LABELS["draft"], font=("Segoe UI", 9, "bold"), padx=10, pady=4)
        status_chip.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=detail_var, style="Body.TLabel", wraplength=620, justify="left").grid(row=0, column=1, sticky="w", padx=(10, 0))

        self._set_chip_style(status_chip, "draft")
        row = SessionRowWidgets(
            row_id=row_id,
            frame=row_frame,
            index_var=index_var,
            url_var=url_var,
            image_var=image_var,
            status_var=status_var,
            detail_var=detail_var,
            url_entry=url_entry,
            image_entry=image_entry,
            browse_button=browse_button,
            remove_button=remove_button,
            open_button=open_button,
            status_chip=status_chip,
        )
        self.rows.append(row)
        self._renumber_rows()

    def _remove_row(self, row_id: str) -> None:
        if self.running:
            return
        if len(self.rows) == 1:
            row = self._find_row_by_id(row_id)
            if row is not None:
                row.url_var.set("")
                row.image_var.set("")
                row.output_dir = None
                row.open_button.configure(state="disabled")
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
        path = filedialog.askopenfilename(
            title="Chọn ảnh sản phẩm",
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg"), ("PNG", "*.png"), ("JPEG", "*.jpg;*.jpeg")],
        )
        if not path:
            return
        row = self._find_row_by_id(row_id)
        if row is not None:
            row.image_var.set(path)

    def _browse_output_root(self) -> None:
        selected = filedialog.askdirectory(title="Chọn thư mục output session")
        if selected:
            self.output_root_var.set(selected)

    def _browse_cookies_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Ch?n cookies.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if selected:
            self.cookies_file_var.set(selected)

    def _start_session(self) -> None:
        if self.running:
            return
        self.latest_result = None
        self.current_session_dir = None
        self.summary_path_var.set("Đang chuẩn bị session...")
        self.open_session_button.configure(state="disabled")
        session_spec = self._build_session_spec()
        self._clear_all_row_states_for_run()
        self._set_running_state(True)
        self._set_session_status("validating_session", "Đang kiểm tra toàn bộ danh sách trước khi chạy.")
        worker = threading.Thread(target=self._run_session_worker, args=(session_spec,), daemon=True)
        worker.start()

    def _build_session_spec(self) -> SessionSpec:
        output_root_text = self.output_root_var.get().strip() or str(self.config.default_output_root)
        items = []
        for row in self.rows:
            image_text = row.image_var.get().strip()
            product_image = Path(image_text) if image_text else None
            items.append(
                SessionItemSpec(
                    row_id=row.row_id,
                    source_video_url=row.url_var.get().strip(),
                    product_image=product_image,
                )
            )
        cookies_text = self.cookies_file_var.get().strip()
        return SessionSpec(
            items=items,
            output_root_dir=Path(output_root_text),
            session_name=(self.session_name_var.get().strip() or None),
            cookies_file=Path(cookies_text) if cookies_text else None,
        )

    def _run_session_worker(self, session_spec: SessionSpec) -> None:
        result = self.orchestrator.run(session_spec, event_callback=self._queue_event)
        self._queue_event(SessionEvent(event_type="worker_result", payload={"result": result}))

    def _clear_all_row_states_for_run(self) -> None:
        for row in self.rows:
            row.output_dir = None
            row.open_button.configure(state="disabled")
            self._set_row_status(row, "queued", "Đang chờ trong queue.")
        self.summary_counts_var.set("%d item | 0 hoàn tất | 0 lỗi" % len(self.rows))
        self._append_log("Session queued with %d item(s)." % len(self.rows), reset=True)

    def _set_running_state(self, is_running: bool) -> None:
        self.running = is_running
        state = "disabled" if is_running else "normal"
        self.add_row_button.configure(state=state)
        self.run_button.configure(state=state)
        self.output_root_entry.configure(state=state)
        self.output_browse_button.configure(state=state)
        self.cookies_file_entry.configure(state=state)
        self.cookies_browse_button.configure(state=state)
        self.session_name_entry.configure(state=state)
        for row in self.rows:
            row.url_entry.configure(state=state)
            row.image_entry.configure(state=state)
            row.browse_button.configure(state=state)
            row.remove_button.configure(state=state)

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

    def _handle_event(self, event: SessionEvent) -> None:
        if event.event_type == "session_started":
            self._set_session_status(event.status or "draft", event.message or "Session created.")
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
            if row is not None:
                self._set_row_status(row, event.status or "queued", event.message or "Đang chờ xử lý.")
            return
        if event.event_type == "item_stage":
            row = self._find_row_by_index(event.item_index)
            if row is not None:
                detail = STAGE_LABELS.get(event.stage or "", event.message or "Đang xử lý.")
                self._set_row_status(row, event.status or "processing", detail)
            return
        if event.event_type == "item_warning":
            prefix = "Item %d" % ((event.item_index or 0) + 1)
            self._append_log("%s warning: %s" % (prefix, event.message or ""))
            return
        if event.event_type == "item_completed":
            row = self._find_row_by_index(event.item_index)
            if row is not None:
                row.output_dir = event.payload.get("output_dir")
                row.open_button.configure(state="normal" if row.output_dir else "disabled")
                self._set_row_status(row, "completed", "Đã hoàn tất và xuất artifact cho item này.")
            self._append_log("Item %d completed." % ((event.item_index or 0) + 1))
            return
        if event.event_type == "item_failed":
            row = self._find_row_by_index(event.item_index)
            if row is not None:
                row.output_dir = event.payload.get("output_dir")
                row.open_button.configure(state="normal" if row.output_dir else "disabled")
                self._set_row_status(row, "failed", event.message or "Item failed.")
            self._append_log("Item %d failed: %s" % (((event.item_index or 0) + 1), event.message or ""))
            return
        if event.event_type == "session_progress":
            total = event.payload.get("total_items", len(self.rows))
            completed = event.payload.get("completed_items", 0)
            failed = event.payload.get("failed_items", 0)
            self.summary_counts_var.set("%d item | %d hoàn tất | %d lỗi" % (total, completed, failed))
            return
        if event.event_type == "session_completed":
            self._set_session_status(event.status or "completed_with_success", event.message or "Session completed.")
            payload = event.payload or {}
            self.summary_counts_var.set(
                "%d item | %d hoàn tất | %d lỗi" % (
                    payload.get("item_count_total", len(self.rows)),
                    payload.get("item_count_completed", 0),
                    payload.get("item_count_failed", 0),
                )
            )
            self._append_log("Session completed.")
            return
        if event.event_type == "session_failed":
            self._set_session_status("failed_session", event.message or "Session failed.")
            self._append_log("Session failed: %s" % (event.message or ""))
            return
        if event.event_type == "worker_result":
            self._finalize_result(event.payload.get("result"))

    def _finalize_result(self, result: Optional[SessionResult]) -> None:
        self.latest_result = result
        self._set_running_state(False)
        if result is None:
            self._set_session_status("failed_session", "Không nhận được kết quả session.")
            messagebox.showerror("Session failed", "Không nhận được kết quả session.")
            return
        if result.row_errors:
            self._apply_row_errors(result.row_errors)
            messagebox.showwarning("Kiểm tra lại input", "Session chưa thể chạy vì còn dòng invalid. Xem trạng thái từng dòng để sửa.")
            return
        if result.artifacts and result.artifacts.session_dir:
            self.current_session_dir = str(result.artifacts.session_dir)
            self.summary_path_var.set(str(result.artifacts.summary_path))
            self.open_session_button.configure(state="normal")
        else:
            self.summary_path_var.set("Session không tạo được summary artifact.")
            self.open_session_button.configure(state="disabled")
        if result.status == "completed_with_success":
            messagebox.showinfo("Session hoàn tất", "Tất cả item đã xử lý xong thành công.")
        elif result.status == "completed_with_partial_failure":
            messagebox.showwarning("Session hoàn tất", "Session đã xong nhưng có ít nhất một item bị lỗi. Xem session summary để biết chi tiết.")
        elif result.status == "failed_session":
            messagebox.showerror("Session failed", "Session không hoàn tất được. Xem log bên phải để biết chi tiết.")

    def _apply_row_errors(self, row_errors: Dict[int, List[str]]) -> None:
        for index, messages in row_errors.items():
            row = self._find_row_by_index(index)
            if row is not None:
                self._set_row_status(row, "invalid", " | ".join(messages))
        for index, row in enumerate(self.rows):
            if index not in row_errors and row.status_var.get() != STATUS_LABELS["completed"]:
                self._set_row_status(row, "draft", "Sẵn sàng sau khi sửa các dòng lỗi khác.")

    def _set_row_status(self, row: SessionRowWidgets, status: str, detail: str) -> None:
        row.status_var.set(STATUS_LABELS.get(status, status))
        row.detail_var.set(detail)
        row.status_chip.configure(text=row.status_var.get())
        self._set_chip_style(row.status_chip, status)

    def _set_session_status(self, status: str, detail: str) -> None:
        self.session_status_var.set(STATUS_LABELS.get(status, status))
        self.session_detail_var.set(detail)
        self.session_chip.configure(text=self.session_status_var.get())
        self._set_chip_style(self.session_chip, status)

    def _set_chip_style(self, widget: tk.Label, status: str) -> None:
        background, foreground = STATUS_COLORS.get(status, STATUS_COLORS["draft"])
        widget.configure(bg=background, fg=foreground)

    def _append_log(self, message: str, reset: bool = False) -> None:
        if not message:
            return
        self.log_text.configure(state="normal")
        if reset:
            self.log_text.delete("1.0", "end")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_row_output(self, row_id: str) -> None:
        row = self._find_row_by_id(row_id)
        if row is not None:
            self._open_path(row.output_dir)

    def _open_path(self, path_value: Optional[str]) -> None:
        if not path_value:
            return
        try:
            os.startfile(path_value)  # type: ignore[attr-defined]
        except Exception as exc:
            self.logger.exception("Unable to open path %s", path_value)
            messagebox.showerror("Không thể mở thư mục", str(exc))

    def _find_row_by_id(self, row_id: str) -> Optional[SessionRowWidgets]:
        for row in self.rows:
            if row.row_id == row_id:
                return row
        return None

    def _find_row_by_index(self, index: Optional[int]) -> Optional[SessionRowWidgets]:
        if index is None:
            return None
        if 0 <= index < len(self.rows):
            return self.rows[index]
        return None


def launch_ui(config: Optional[PipelineConfig] = None) -> int:
    root = tk.Tk()
    app = EditorApplication(root, config=config)
    root.mainloop()
    return 0


