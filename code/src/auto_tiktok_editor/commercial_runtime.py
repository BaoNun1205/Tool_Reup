from __future__ import annotations

import os
from pathlib import Path
import sys

from auto_tiktok_editor.config import PipelineConfig


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def ensure_runtime_allowed(config: PipelineConfig, *, surface: str) -> None:
    if config.require_frozen_build and not config.runtime_is_frozen and not _env_flag("AUTO_EDITOR_ALLOW_SOURCE_RUNTIME", False):
        raise RuntimeError(
            "Ban thuong mai chi duoc phep chay tu file .exe dong goi. "
            "Runtime hien tai cho '%s' dang la source code." % surface
        )


def ensure_local_telegram_allowed(config: PipelineConfig, *, surface: str) -> None:
    if config.allow_local_telegram:
        return
    raise RuntimeError(
        "Tinh nang Telegram local da bi tat trong ban thuong mai. "
        "Khong the su dung '%s' tren may khach." % surface
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
