from __future__ import annotations

import os
from pathlib import Path
import sys

from auto_tiktok_editor.config import PipelineConfig


def ensure_local_telegram_allowed(config: PipelineConfig, *, surface: str) -> None:
    if config.allow_local_telegram:
        return
    raise RuntimeError(
        "Telegram bot is not configured. Set AUTO_EDITOR_TELEGRAM_BOT_TOKEN before running '%s'." % surface
    )


def configure_tk_environment() -> None:
    current_tcl = os.getenv("TCL_LIBRARY", "").strip()
    current_tk = os.getenv("TK_LIBRARY", "").strip()
    if current_tcl and current_tk:
        return

    runtime_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    base_prefix = Path(sys.base_prefix).resolve()

    tcl_candidates = []
    tk_candidates = []
    if runtime_dir is not None:
        tcl_candidates.extend([runtime_dir / "tcl", runtime_dir / "tcl8" / "8.6"])
        tk_candidates.extend([runtime_dir / "tk", runtime_dir / "tcl8" / "8.6"])
    tcl_candidates.extend(
        [
            base_prefix / "tcl" / "tcl8.6",
            base_prefix / "tcl" / "tcl8.7",
            base_prefix / "tcl",
        ]
    )
    tk_candidates.extend(
        [
            base_prefix / "tcl" / "tk8.6",
            base_prefix / "tcl" / "tk8.7",
            base_prefix / "tk",
            base_prefix / "tcl" / "tk",
        ]
    )

    if not current_tcl:
        for candidate in tcl_candidates:
            if (candidate / "init.tcl").exists():
                os.environ["TCL_LIBRARY"] = str(candidate)
                break

    if not current_tk:
        for candidate in tk_candidates:
            if (candidate / "tk.tcl").exists():
                os.environ["TK_LIBRARY"] = str(candidate)
                break
