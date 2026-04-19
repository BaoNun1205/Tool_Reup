"""Tkinter login dialog for license authentication."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Optional

from auto_tiktok_editor import __version__
from auto_tiktok_editor.license.exceptions import (
    LicenseAuthenticationRequired,
    LicenseError,
    LicenseServerUnavailable,
    LicenseVerificationError,
)
from auto_tiktok_editor.license.guard import LicenseGuard
from auto_tiktok_editor.license.models import VerifiedLicenseSession


PALETTE = {
    "bg": "#08111F",
    "card": "#0F1B2E",
    "border": "#243755",
    "text": "#F5F7FB",
    "muted": "#91A3BF",
    "accent": "#1ED3A5",
    "accent_hover": "#19B88F",
    "input_bg": "#182740",
}


class LicenseLoginDialog(object):
    def __init__(self, guard: LicenseGuard, logger: Optional[logging.Logger] = None):
        self.guard = guard
        self.logger = logger or logging.getLogger("auto_tiktok_editor.license_ui")
        self.result = None  # type: Optional[VerifiedLicenseSession]
        self.root = tk.Tk()
        self.root.title("Đăng nhập tài khoản")
        self.root.geometry("620x470")
        self.root.minsize(580, 450)
        self.root.configure(bg=PALETTE["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Đang kiểm tra online phiên đăng nhập trên máy này...")
        self.logger.info("License login dialog opened.")

        self._build_styles()
        self._build_layout()
        self.root.after(10, self._try_cached_session)

    def show(self) -> Optional[VerifiedLicenseSession]:
        self.root.mainloop()
        return self.result

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Login.TFrame", background=PALETTE["bg"])
        style.configure("Card.TFrame", background=PALETTE["card"])
        style.configure(
            "Title.TLabel",
            background=PALETTE["card"],
            foreground=PALETTE["text"],
            font=("Segoe UI Semibold", 22, "bold"),
        )
        style.configure(
            "Body.TLabel",
            background=PALETTE["card"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Field.TLabel",
            background=PALETTE["card"],
            foreground=PALETTE["text"],
            font=("Segoe UI Semibold", 10, "bold"),
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI Semibold", 10, "bold"),
            padding=(16, 10),
            background=PALETTE["accent"],
            foreground="#06251F",
            borderwidth=0,
        )
        style.map("Primary.TButton", background=[("active", PALETTE["accent_hover"])])
        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 10),
            padding=(14, 9),
            background=PALETTE["input_bg"],
            foreground=PALETTE["text"],
            borderwidth=0,
        )
        style.configure(
            "TEntry",
            padding=10,
            fieldbackground=PALETTE["input_bg"],
            background=PALETTE["input_bg"],
            foreground=PALETTE["text"],
            insertcolor=PALETTE["text"],
            bordercolor=PALETTE["border"],
            lightcolor=PALETTE["border"],
            darkcolor=PALETTE["border"],
            relief="solid",
        )

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="Login.TFrame", padding=24)
        outer.pack(fill="both", expand=True)

        card = ttk.Frame(outer, style="Card.TFrame", padding=28)
        card.pack(fill="both", expand=True)
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text="Đăng nhập để sử dụng tool", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            card,
            text=(
                "Tool này chỉ hoạt động với tài khoản do admin cấp. "
                "Tài khoản sẽ được gắn với máy hiện tại và tự động khóa khi hết hạn."
            ),
            style="Body.TLabel",
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 18))

        ttk.Label(card, text="Tài khoản", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        self.username_entry = ttk.Entry(card, textvariable=self.username_var)
        self.username_entry.grid(row=3, column=0, sticky="ew", pady=(8, 14))

        ttk.Label(card, text="Mật khẩu", style="Field.TLabel").grid(row=4, column=0, sticky="w")
        self.password_entry = ttk.Entry(card, textvariable=self.password_var, show="*")
        self.password_entry.grid(row=5, column=0, sticky="ew", pady=(8, 14))

        ttk.Label(
            card,
            textvariable=self.status_var,
            style="Body.TLabel",
            wraplength=500,
            justify="left",
        ).grid(row=6, column=0, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=7, column=0, sticky="ew", pady=(26, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

        self.cancel_button = ttk.Button(buttons, text="Thoát", style="Secondary.TButton", command=self._close)
        self.cancel_button.grid(row=0, column=0, sticky="w")
        self.login_button = ttk.Button(buttons, text="Đăng nhập", style="Primary.TButton", command=self._submit)
        self.login_button.grid(row=0, column=1, sticky="e")

        self.root.bind("<Return>", lambda event: self._submit())

    def _try_cached_session(self) -> None:
        self.logger.info("Trying cached license session.")
        try:
            session = self.guard.ensure_online_session()
        except (LicenseAuthenticationRequired, LicenseVerificationError):
            self.logger.info("No usable cached session was found.")
            self.status_var.set("Nhập tài khoản do admin cấp để kích hoạt máy này.")
            self.username_entry.focus_set()
            return
        except LicenseServerUnavailable:
            self.logger.warning("License server unavailable while checking cached session.")
            self.status_var.set("Không kết nối được server và máy này chưa có phiên hợp lệ. Hãy thử lại khi có mạng.")
            self.username_entry.focus_set()
            return
        except LicenseError as exc:
            self.logger.warning("Cached session rejected: %s", exc)
            self.status_var.set(str(exc))
            self.username_entry.focus_set()
            return
        except Exception:
            self.logger.exception("Unexpected cached-session error.")
            try:
                self.guard.store.clear()
            except Exception:
                pass
            self.status_var.set("Phiên đăng nhập cũ trên máy này không còn dùng được. Hãy đăng nhập lại.")
            self.username_entry.focus_set()
            return
        self.logger.info("Cached session accepted for user %s.", session.username)
        self.result = session
        self.root.destroy()

    def _submit(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            self.status_var.set("Cần nhập đầy đủ tài khoản và mật khẩu.")
            return
        self.login_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status_var.set("Đang xác thực tài khoản với server...")
        self.root.update_idletasks()
        self.logger.info("Submitting interactive login for user %s.", username)
        try:
            session = self.guard.login(username=username, password=password, app_version=__version__)
        except LicenseError as exc:
            self.logger.warning("License login failed: %s", exc)
            self.status_var.set(str(exc))
            self.password_var.set("")
            self.password_entry.focus_set()
            self.login_button.configure(state="normal")
            self.cancel_button.configure(state="normal")
            return
        except Exception as exc:
            self.logger.exception("Unexpected license login error.")
            self.status_var.set("Không thể hoàn tất đăng nhập trên máy này. Chi tiết: %s" % exc)
            self.password_var.set("")
            self.password_entry.focus_set()
            self.login_button.configure(state="normal")
            self.cancel_button.configure(state="normal")
            return
        self.logger.info("Interactive login succeeded for user %s.", session.username)
        self.result = session
        self.root.destroy()

    def _close(self) -> None:
        self.logger.info("License login dialog closed by user.")
        self.result = None
        self.root.destroy()


def ensure_ui_license_session(guard: LicenseGuard, logger: Optional[logging.Logger] = None) -> Optional[VerifiedLicenseSession]:
    dialog = LicenseLoginDialog(guard=guard, logger=logger)
    return dialog.show()
