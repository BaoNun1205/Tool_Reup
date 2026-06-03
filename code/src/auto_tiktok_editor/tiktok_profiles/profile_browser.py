"""Playwright browser control for TikTok persistent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import sys
import time
import tempfile
import types

from auto_tiktok_editor.tiktok_profiles.models import TikTokAccount, TikTokVideo
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager, normalize_hashtags, split_caption_and_hashtags


TIKTOK_STUDIO_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
AUTO_SUBMIT_TIKTOK_POSTS = os.environ.get("AUTO_TIKTOK_PROFILE_AUTO_SUBMIT", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


@dataclass
class BrowserSession:
    account_id: int
    context: object
    page: object

    def close(self) -> None:
        self.context.close()

    def is_alive(self) -> bool:
        try:
            if self.page is not None and not self.page.is_closed():
                return True
        except Exception:
            pass
        try:
            return any(not page.is_closed() for page in self.context.pages)
        except Exception:
            return False


@dataclass
class PostAutomationResult:
    status: str
    message: str


class TikTokProfileBrowser:
    def __init__(self, manager: TikTokProfileManager, channel: str = "chrome") -> None:
        self.manager = manager
        self.channel = channel
        self.sessions: dict[int, BrowserSession] = {}
        self.playwright = None

    def open_profile(self, account: TikTokAccount) -> None:
        self._ensure_session(account)

    def open_tiktok_studio(self, account: TikTokAccount) -> str:
        last_error = None
        for _attempt in range(2):
            session = self._ensure_session(account)
            try:
                session.page.goto(TIKTOK_STUDIO_UPLOAD_URL, wait_until="domcontentloaded", timeout=45000)
                return classify_tiktok_studio_page(session.page)
            except Exception as exc:
                last_error = exc
                if not _looks_like_closed_browser_error(exc):
                    raise
                self._discard_session(account.id)
        raise last_error if last_error is not None else RuntimeError("Browser could not be opened.")

    def upload_video_file(self, account: TikTokAccount, video_path: Path | str) -> str:
        resolved_video_path = Path(video_path).expanduser().resolve()
        if not resolved_video_path.exists() or not resolved_video_path.is_file():
            raise RuntimeError("Video file does not exist: %s" % resolved_video_path)

        status = self.open_tiktok_studio(account)
        if status != "live":
            return status

        session = self._ensure_session(account)
        page = session.page
        page.wait_for_timeout(1500)
        self.manager.add_log("info", "upload_step", "Looking for file input.", account_id=account.id)
        if _set_file_on_any_input(page, resolved_video_path):
            _dismiss_optional_popups(page, strict=True)
            self.manager.add_log("info", "upload_step", "File selected through file input.", account_id=account.id)
            return "file_selected"

        self.manager.add_log("info", "upload_step", "Trying TikTok upload button.", account_id=account.id)
        try:
            page.bring_to_front()
        except Exception:
            pass

        self.manager.add_log("info", "upload_step", "Trying Playwright file chooser clickers.", account_id=account.id)
        for clicker_name, clicker in _upload_button_clickers(page):
            try:
                with page.expect_file_chooser(timeout=2500) as file_chooser_info:
                    clicker()
                file_chooser_info.value.set_files(str(resolved_video_path))
                _dismiss_optional_popups(page, strict=True)
                self.manager.add_log("info", "upload_step", "File selected through Playwright file chooser: %s." % clicker_name, account_id=account.id)
                page.wait_for_timeout(1500)
                return "file_selected"
            except Exception as exc:
                self.manager.add_log("debug", "upload_step", "File chooser clicker failed (%s): %s" % (clicker_name, exc), account_id=account.id)
                continue

        self.manager.add_log("info", "upload_step", "Trying DOM coordinate click on Select videos.", account_id=account.id)
        try:
            _click_upload_by_dom_coordinates(page)
            page.wait_for_timeout(1000)
            if _fill_windows_open_dialog(resolved_video_path):
                _dismiss_optional_popups(page, strict=True)
                self.manager.add_log("info", "upload_step", "File selected through Windows dialog after DOM click.", account_id=account.id)
                page.wait_for_timeout(2000)
                return "file_selected"
        except Exception as exc:
            self.manager.add_log("warning", "upload_step", "DOM coordinate click failed: %s" % exc, account_id=account.id)
        raise RuntimeError("Could not find TikTok upload file input.")

    def post_video(self, account: TikTokAccount, video: TikTokVideo) -> PostAutomationResult:
        resolved_video_path = self.manager.resolve_video_path(video)
        if not resolved_video_path.exists() or not resolved_video_path.is_file():
            raise RuntimeError("Video file does not exist: %s" % resolved_video_path)

        status = self.upload_video_file(account, resolved_video_path)
        if status != "file_selected":
            return PostAutomationResult(status=status, message="TikTok Studio status is %s." % status)

        session = self._ensure_session(account)
        page = session.page
        _wait_for_upload_details(page)
        self.manager.add_log("info", "auto_post_step", "Filling description and hashtags for video %s." % video.id, account_id=account.id, video_id=video.id)
        _fill_video_caption(page, video)
        self.manager.add_log("info", "auto_post_step", "Description and hashtags filled for video %s." % video.id, account_id=account.id, video_id=video.id)

        if video.product_id:
            try:
                self.manager.add_log(
                    "info",
                    "product_step",
                    "Attaching product %s for video %s." % (video.product_id, video.id),
                    account_id=account.id,
                    video_id=video.id,
                )
                _attach_product(page, video.product_id)
                self.manager.add_log(
                    "info",
                    "product_step",
                    "Product %s attached for video %s." % (video.product_id, video.id),
                    account_id=account.id,
                    video_id=video.id,
                )
            except Exception as exc:
                return PostAutomationResult(status="product_error", message=str(exc))

        if video.publish_mode == "scheduled":
            _set_schedule(page, video.scheduled_at)

        try:
            _wait_for_safety_checks_ready(
                page,
                require_copyright=True,
                require_quick=True,
            )
            if not AUTO_SUBMIT_TIKTOK_POSTS:
                return PostAutomationResult(
                    status="prepared",
                    message="Auto submit disabled; Schedule/Post was not clicked.",
                )
            _click_submit(page, scheduled=video.publish_mode == "scheduled")
        except Exception as exc:
            return PostAutomationResult(status="selector_error", message=str(exc))

        page.wait_for_timeout(3000)
        return PostAutomationResult(
            status="scheduled" if video.publish_mode == "scheduled" else "posted",
            message="Submit clicked for %s." % account.name,
        )

    def close_all(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            finally:
                self.playwright = None

    def close_account(self, account_id: int) -> None:
        self._discard_session(account_id)

    def _ensure_session(self, account: TikTokAccount) -> BrowserSession:
        session = self.sessions.get(account.id)
        if session is not None:
            if session.is_alive():
                try:
                    if session.page.is_closed():
                        session.page = _first_live_page(session.context) or session.context.new_page()
                except Exception:
                    session.page = _first_live_page(session.context) or session.context.new_page()
                return session
            self._discard_session(account.id)

        playwright = self._ensure_playwright()

        profile_path = self.manager.resolve_profile_path(account)
        profile_path.mkdir(parents=True, exist_ok=True)
        try:
            context = self._launch_context(playwright, profile_path)
            page = context.pages[0] if context.pages else context.new_page()
            session = BrowserSession(account_id=account.id, context=context, page=page)
            self.sessions[account.id] = session
            return session
        except Exception:
            raise

    def _ensure_playwright(self) -> object:
        if self.playwright is not None:
            return self.playwright
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install -e . && python -m playwright install chromium"
            ) from exc
        self.playwright = sync_playwright().start()
        return self.playwright

    def _discard_session(self, account_id: int) -> None:
        session = self.sessions.pop(account_id, None)
        if session is None:
            return
        try:
            session.close()
        except Exception:
            pass

    def _launch_context(self, playwright: object, profile_path: Path) -> object:
        launch_kwargs = {
            "user_data_dir": str(profile_path),
            "headless": False,
            "no_viewport": True,
            "args": ["--disable-blink-features=AutomationControlled", "--disable-session-crashed-bubble"],
        }
        if self.channel:
            try:
                return playwright.chromium.launch_persistent_context(channel=self.channel, **launch_kwargs)
            except Exception:
                pass
        return playwright.chromium.launch_persistent_context(**launch_kwargs)


def classify_tiktok_studio_page(page: object) -> str:
    try:
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(3000)
        except Exception:
            pass

        current_url = (getattr(page, "url", "") or "").lower()
        title = ""
        body_text = ""
        try:
            title = (page.title(timeout=3000) or "").lower()
        except Exception:
            title = ""
        try:
            body_text = (page.locator("body").inner_text(timeout=5000) or "").lower()
        except Exception:
            body_text = ""

        combined = "%s\n%s\n%s" % (current_url, title, body_text)
        if _contains_any(combined, ("checkpoint", "security check", "verify", "verification", "captcha")):
            return "checkpoint"
        if _contains_any(
            combined,
            (
                "/login",
                "log in",
                "login",
                "sign up",
                "continue with google",
                "continue with facebook",
                "phone / email / username",
            ),
        ):
            return "need_login"

        try:
            if page.locator("input[type='file']").count() > 0:
                return "live"
        except Exception:
            pass

        if "tiktokstudio/upload" in current_url and _contains_any(
            combined,
            ("upload", "select video", "post", "caption", "video"),
        ):
            return "live"
        if "tiktokstudio/upload" in current_url:
            return "live"
        return "error"
    except Exception:
        return "error"


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _dismiss_optional_popups(page: object, strict: bool = False) -> None:
    page.wait_for_timeout(300)
    labels = (
        "Got it",
        "OK",
        "Okay",
        "Đã hiểu",
        "Da hieu",
        "Tôi đã hiểu",
        "Toi da hieu",
    )
    for _attempt in range(3):
        clicked = False
        for label in labels:
            try:
                button = page.get_by_role("button", name=re.compile(r"^\s*%s\s*$" % re.escape(label), re.I)).first
                if button.is_visible(timeout=500):
                    button.click(timeout=1000)
                    page.wait_for_timeout(300)
                    clicked = True
                    break
            except Exception:
                pass
            if strict:
                continue
            try:
                text = page.get_by_text(label, exact=False).first
                if text.is_visible(timeout=500):
                    text.click(timeout=1000)
                    page.wait_for_timeout(300)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            return


def _set_file_on_any_input(page: object, file_path: Path) -> bool:
    selectors = (
        "input[type='file']",
        "input[accept*='video']",
        "input[accept*='mp4']",
        "input",
    )
    for context in _page_contexts(page):
        for selector in selectors:
            try:
                locators = context.locator(selector)
                count = min(locators.count(), 20)
                for index in range(count):
                    locator = locators.nth(index)
                    try:
                        locator.set_input_files(str(file_path), timeout=2000)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


def _upload_button_clickers(page: object):
    labels = (
        "Select videos",
        "Select video",
        "Upload",
        "Select file",
        "Choose file",
        "Chọn video",
        "Chon video",
        "Tải video lên",
        "Tai video len",
    )

    def by_role(label: str):
        return lambda: page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first.click(timeout=1000)

    def by_text(label: str):
        return lambda: page.get_by_text(label, exact=False).first.click(timeout=1000)

    def by_selector(selector: str):
        return lambda: page.locator(selector).first.click(timeout=1000)

    clickers = []
    for label in labels:
        clickers.append(by_role(label))
    for label in labels:
        clickers.append(by_text(label))
    clickers.extend(
        [
            by_selector("button:has-text('Select videos')"),
            by_selector("button:has-text('Select video')"),
            by_selector("button:has-text('Upload')"),
            by_selector("[role='button']:has-text('Select videos')"),
            by_selector("[role='button']:has-text('Upload')"),
        ]
    )
    return clickers


def _upload_button_clickers(page: object):
    labels = (
        "Select videos",
        "Select video",
        "Upload",
        "Select file",
        "Choose videos",
        "Choose video",
        "Choose file",
        "Chon video",
        "Tai video len",
    )

    def by_role(label: str):
        return lambda: page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first.click(timeout=1000)

    def by_text(label: str):
        return lambda: page.get_by_text(label, exact=False).first.click(timeout=1000)

    def by_selector(selector: str):
        return lambda: page.locator(selector).first.click(timeout=1000)

    clickers = []
    for label in labels:
        clickers.append(("role button %s" % label, by_role(label)))
    for label in labels:
        clickers.append(("text %s" % label, by_text(label)))
    for selector in (
        "button:has-text('Select videos')",
        "button:has-text('Select video')",
        "button:has-text('Upload')",
        "[role='button']:has-text('Select videos')",
        "[role='button']:has-text('Upload')",
    ):
        clickers.append(("selector %s" % selector, by_selector(selector)))
    return clickers


def _click_upload_by_dom_coordinates(page: object) -> None:
    box = page.evaluate(
        """
        () => {
            const needles = ['Select videos', 'Select video', 'Upload', 'Chọn video', 'Tải video lên'];
            const candidates = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
            for (const el of candidates) {
                const text = (el.innerText || el.textContent || '').trim();
                if (!text) continue;
                if (!needles.some((needle) => text.toLowerCase().includes(needle.toLowerCase()))) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width > 20 && rect.height > 20) {
                    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                }
            }
            return null;
        }
        """
    )
    if not box:
        raise RuntimeError("Upload button coordinates not found.")
    page.mouse.click(float(box["x"]), float(box["y"]))


def _page_contexts(page: object) -> list[object]:
    contexts = [page]
    try:
        contexts.extend(page.frames)
    except Exception:
        pass
    return contexts


def _context_name(context: object) -> str:
    try:
        url = getattr(context, "url", "")
        if url:
            return "frame:%s" % url
    except Exception:
        pass
    return "page"


def _locator_clicker(locator: object, page: object):
    def click() -> None:
        try:
            locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        try:
            locator.click(timeout=1500)
            return
        except Exception:
            pass
        box = locator.bounding_box(timeout=1000)
        if not box:
            raise RuntimeError("locator has no bounding box")
        page.mouse.click(float(box["x"] + box["width"] / 2), float(box["y"] + box["height"] / 2))

    return click


def _upload_button_clickers(page: object):
    button_labels = (
        "Select videos",
        "Select video",
        "Select files",
        "Select file",
        "Choose videos",
        "Choose video",
        "Choose file",
        "Chon video",
        "Tai video len",
        "Chọn video",
        "Chọn tệp",
        "Tải video lên",
    )
    broad_labels = button_labels + ("Upload",)
    selectors = (
        "button:has-text('Select videos')",
        "button:has-text('Select video')",
        "button:has-text('Choose file')",
        "[role='button']:has-text('Select videos')",
        "[role='button']:has-text('Select video')",
        "[role='button']:has-text('Choose file')",
        "button:has-text('Chọn video')",
        "[role='button']:has-text('Chọn video')",
    )

    clickers = []
    for context in _page_contexts(page):
        context_name = _context_name(context)
        for label in button_labels:
            locator = context.get_by_role("button", name=re.compile(r"^\s*%s\s*$" % re.escape(label), re.I)).first
            clickers.append(("%s role button %s" % (context_name, label), _locator_clicker(locator, page)))
        for selector in selectors:
            locator = context.locator(selector).first
            clickers.append(("%s selector %s" % (context_name, selector), _locator_clicker(locator, page)))
        for label in broad_labels:
            locator = context.get_by_text(label, exact=False).first
            clickers.append(("%s text %s" % (context_name, label), _locator_clicker(locator, page)))
    return clickers


def _click_upload_by_dom_coordinates(page: object) -> None:
    errors = []
    for context in _page_contexts(page):
        try:
            box = context.evaluate(
                """
                () => {
                    const needles = ['Select videos', 'Select video', 'Choose file', 'Chọn video', 'Tải video lên'];
                    const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, div, span, p'));
                    for (const el of candidates) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (!text) continue;
                        if (!needles.some((needle) => text.toLowerCase().includes(needle.toLowerCase()))) continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 20 && rect.height > 20) {
                            return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, text };
                        }
                    }
                    return null;
                }
                """
            )
            if box:
                page.mouse.click(float(box["x"]), float(box["y"]))
                return
        except Exception as exc:
            errors.append(str(exc))
    details = "; ".join(errors[:3])
    raise RuntimeError("Upload button coordinates not found.%s" % (" %s" % details if details else ""))


def _click_upload_viewport(page: object) -> None:
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        viewport = page.viewport_size
    except Exception:
        viewport = None
    if not viewport:
        viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")

    width = float(viewport["width"])
    height = float(viewport["height"])
    page.mouse.click(width * 0.58, height * 0.63)


def _upload_button_clickers(page: object):
    labels = (
        "Select videos",
        "Select video",
        "Upload",
        "Select file",
        "Choose videos",
        "Choose video",
        "Choose file",
        "Chon video",
        "Tai video len",
    )

    def by_role(label: str):
        return lambda: page.get_by_role("button", name=re.compile(re.escape(label), re.I)).first.click(timeout=1000)

    def by_text(label: str):
        return lambda: page.get_by_text(label, exact=False).first.click(timeout=1000)

    def by_selector(selector: str):
        return lambda: page.locator(selector).first.click(timeout=1000)

    clickers = []
    for label in labels:
        clickers.append(("role button %s" % label, by_role(label)))
    for label in labels:
        clickers.append(("text %s" % label, by_text(label)))
    for selector in (
        "button:has-text('Select videos')",
        "button:has-text('Select video')",
        "button:has-text('Upload')",
        "[role='button']:has-text('Select videos')",
        "[role='button']:has-text('Upload')",
    ):
        clickers.append(("selector %s" % selector, by_selector(selector)))
    return clickers


def _first_live_page(context: object):
    try:
        for page in context.pages:
            try:
                if not page.is_closed():
                    return page
            except Exception:
                continue
    except Exception:
        return None
    return None


def _looks_like_closed_browser_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "target page, context or browser has been closed" in message
        or "browser has been closed" in message
        or "context has been closed" in message
        or "page has been closed" in message
    )


def _fill_windows_open_dialog(file_path: Path) -> bool:
    _prepare_windows_automation_cache()
    try:
        from pywinauto import Desktop  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Could not find TikTok upload file input. Install pywinauto in .venv so the tool can control the Windows Open dialog."
        )

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            dialog = Desktop(backend="uia").window(title_re="^(Open|Mở|Mo)$")
            if dialog.exists(timeout=1):
                dialog.set_focus()
                edit = dialog.child_window(control_type="Edit")
                edit.set_edit_text(str(file_path))
                try:
                    dialog.child_window(title_re="^(Open|Mở|Mo)$", control_type="Button").click_input()
                except Exception:
                    dialog.type_keys("{ENTER}")
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _prepare_windows_automation_cache() -> None:
    try:
        temp_root = Path.cwd() / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = str(temp_root)
        os.environ["TMP"] = str(temp_root)
        tempfile.tempdir = str(temp_root)
    except Exception:
        pass

    try:
        import comtypes  # type: ignore

        gen_dir = temp_root / "comtypes_gen"
        gen_dir.mkdir(parents=True, exist_ok=True)
        module = sys.modules.get("comtypes.gen")
        if module is None:
            module = types.ModuleType("comtypes.gen")
            sys.modules["comtypes.gen"] = module
        module.__path__ = [str(gen_dir)]  # type: ignore[attr-defined]
        comtypes.gen = module  # type: ignore[attr-defined]
    except Exception:
        return


def _compose_caption(video: TikTokVideo) -> str:
    parts = []
    caption, hashtags = split_caption_and_hashtags(video.caption or "", getattr(video, "hashtags", "") or "")
    if caption:
        parts.append(caption)
    if hashtags:
        parts.append(hashtags)
    return "\n".join(parts)


def _fill_video_caption(page: object, video: TikTokVideo) -> None:
    caption, hashtag_text = split_caption_and_hashtags(video.caption or "", getattr(video, "hashtags", "") or "")
    hashtags = _hashtag_tokens(hashtag_text)
    if caption:
        _fill_caption(page, caption)
    else:
        _clear_caption(page)
    if hashtags:
        _append_hashtags_with_suggestions(page, hashtags, prefix_space=bool(caption))


def _fill_caption(page: object, text: str) -> None:
    if not text:
        return
    _wait_for_upload_details(page)
    if _fill_caption_with_keyboard(page, text):
        return
    if _fill_caption_with_dom(page, text):
        return
    if _force_caption_exact(page, text):
        return
    for selector in (
        "textarea[placeholder*='description' i]",
        "textarea[placeholder*='caption' i]",
        "[contenteditable='true'][data-placeholder*='description' i]",
        "[contenteditable='true'][aria-label*='description' i]",
        "[data-e2e='caption-container'] div[contenteditable='true']",
        "div[contenteditable='true']",
        "textarea",
    ):
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=1000):
                continue
            locator.click(timeout=5000)
            if selector.startswith("textarea"):
                locator.fill(text, timeout=5000)
            else:
                try:
                    locator.fill(text, timeout=5000)
                except Exception:
                    locator.evaluate(
                        """
                        (el, value) => {
                            window.__setTikTokCaptionExact(el, value);
                        }
                        """,
                        text,
                    )
            if _caption_text_is_present(page, text):
                return
        except Exception:
            continue
    if _force_caption_exact(page, text):
        return
    raise RuntimeError("Could not find caption input.")


def _clear_caption(page: object) -> None:
    _wait_for_upload_details(page)
    locator = _find_caption_input(page)
    if locator is None:
        return
    try:
        locator.click(timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    except Exception:
        pass


def _hashtag_tokens(value: str) -> list[str]:
    return [part for part in normalize_hashtags(value).split() if part.startswith("#")]


def _append_hashtags_with_suggestions(page: object, hashtags: list[str], prefix_space: bool = False) -> None:
    locator = _find_caption_input(page)
    if locator is None:
        raise RuntimeError("Could not find caption input for hashtags.")
    try:
        locator.click(timeout=5000)
    except Exception as exc:
        raise RuntimeError("Could not focus caption input for hashtags: %s" % exc) from exc
    for index, hashtag in enumerate(hashtags):
        _move_caption_caret_to_end(page, locator)
        if prefix_space and index == 0:
            _ensure_caption_trailing_space(page, locator)
        page.keyboard.insert_text(hashtag)
        page.wait_for_timeout(1800)
        if not _select_first_hashtag_suggestion(page, hashtag):
            raise RuntimeError("Could not select TikTok hashtag suggestion for %s." % hashtag)
        page.wait_for_timeout(900)


def _ensure_caption_trailing_space(page: object, locator: object) -> None:
    try:
        has_trailing_space = locator.evaluate(
            """
            (el) => {
                if (!el) return null;
                const value = 'value' in el ? el.value : (el.innerText || el.textContent || '');
                if (!value) return true;
                return /\\s$/.test(value);
            }
            """
        )
    except Exception:
        has_trailing_space = None
    if has_trailing_space is True:
        return
    try:
        page.keyboard.insert_text(" ")
    except Exception:
        return


def _move_caption_caret_to_end(page: object, locator: object) -> None:
    try:
        moved = locator.evaluate(
            """
            (el) => {
                if (!el) return false;
                el.focus();
                if ('selectionStart' in el && 'selectionEnd' in el && 'value' in el) {
                    const end = String(el.value || '').length;
                    el.selectionStart = end;
                    el.selectionEnd = end;
                    return true;
                }
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                range.collapse(false);
                selection.removeAllRanges();
                selection.addRange(range);
                return true;
            }
            """
        )
        if moved:
            return
    except Exception:
        pass
    try:
        page.keyboard.press("End")
    except Exception:
        pass


def _select_first_hashtag_suggestion(page: object, hashtag: str) -> bool:
    deadline = time.time() + 15
    while time.time() < deadline:
        if _click_first_hashtag_suggestion(page, hashtag):
            page.wait_for_timeout(900)
            return True
        page.wait_for_timeout(500)
    return False


def _click_first_hashtag_suggestion(page: object, hashtag: str) -> bool:
    try:
        clicked = page.evaluate(
            """
            (tag) => {
                const needle = (tag || '').replace(/^#/, '').toLowerCase();
                if (!needle) return false;
                const active = document.activeElement;
                const activeRect = active ? active.getBoundingClientRect() : null;
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 20
                        && rect.height > 12
                        && rect.top >= 0
                        && rect.bottom <= window.innerHeight
                        && style.visibility !== 'hidden'
                        && style.display !== 'none';
                };
                const insideInput = (el) => Boolean(el.closest("textarea, input, [contenteditable='true']"));
                const textOf = (el) => (el.innerText || el.textContent || '').trim();
                const compact = (value) => textOf(value)
                    .toLowerCase()
                    .replace(/#\\s+/g, '#')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const clickElement = (el) => {
                    const rect = el.getBoundingClientRect();
                    const x = rect.left + Math.min(Math.max(12, rect.width / 2), rect.width - 12);
                    const y = rect.top + Math.min(Math.max(8, rect.height / 2), rect.height - 8);
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: x,
                            clientY: y,
                        }));
                    }
                };
                const nodes = Array.from(document.querySelectorAll("[role='option'], [role='menuitem'], li, button, div, span"));
                const candidates = nodes
                    .filter((el) => visible(el) && !insideInput(el))
                    .map((el) => {
                        const text = compact(el);
                        const role = (el.getAttribute('role') || '').toLowerCase();
                        const className = String(el.className || '').toLowerCase();
                        const rect = el.getBoundingClientRect();
                        const belowInput = activeRect ? rect.top >= activeRect.bottom - 8 : true;
                        const exactTag = text.startsWith('#' + needle);
                        const hasPostCount = /\\bposts?\\b/i.test(text);
                        const rowSized = rect.width >= 280 && rect.height >= 28 && rect.height <= 95;
                        const looksLikeSuggestion = role === 'option'
                            || role === 'menuitem'
                            || hasPostCount
                            || className.includes('suggest')
                            || className.includes('hashtag');
                        let score = 0;
                        if (!text || text.length > 220 || !text.includes(needle)) return null;
                        if (!belowInput) return null;
                        if (!exactTag && !text.includes('#' + needle)) return null;
                        if (!hasPostCount) return null;
                        if (!looksLikeSuggestion && !rowSized) return null;
                        if (hasPostCount) score += 20000;
                        if (rowSized) score += 12000;
                        if (role === 'option') score += 10000;
                        if (role === 'menuitem') score += 8000;
                        if (exactTag) score += 5000;
                        if (className.includes('suggest') || className.includes('mention') || className.includes('hashtag')) score += 2000;
                        if (text.includes('history')) score -= 20000;
                        if (rect.width < 220 && !hasPostCount && role !== 'option' && role !== 'menuitem') score -= 20000;
                        return { el, score, top: rect.top, area: rect.width * rect.height };
                    })
                    .filter(Boolean)
                    .sort((a, b) => (b.score - a.score) || (a.top - b.top) || (a.area - b.area));
                const candidate = candidates[0]?.el;
                if (!candidate) return false;
                candidate.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                clickElement(candidate);
                return true;
            }
            """,
            hashtag,
        )
        return bool(clicked)
    except Exception:
        return False


def _wait_for_upload_details(page: object) -> None:
    _install_caption_helpers(page)
    deadline = time.time() + 90
    last_error = None
    while time.time() < deadline:
        try:
            if page.get_by_text("Description", exact=False).first.is_visible(timeout=700):
                return
        except Exception as exc:
            last_error = exc
        try:
            if page.get_by_text("Uploaded", exact=False).first.is_visible(timeout=700):
                return
        except Exception as exc:
            last_error = exc
        try:
            if page.locator("textarea, [contenteditable='true']").count() > 0:
                return
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(1000)
    raise RuntimeError("Upload details did not appear. Last error: %s" % last_error)


def _install_caption_helpers(page: object) -> None:
    try:
        page.evaluate(
            """
            () => {
                if (window.__setTikTokCaptionExact) return;
                window.__setTikTokCaptionExact = (el, value) => {
                    if (!el) return false;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    el.focus();
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'textarea' || tag === 'input') {
                        const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(el, value);
                        else el.value = value;
                        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.blur();
                        return true;
                    }

                    const selection = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    el.innerHTML = '';
                    el.textContent = '';
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward', data: null }));

                    let ok = false;
                    try {
                        ok = document.execCommand('insertText', false, value);
                    } catch (_error) {
                        ok = false;
                    }
                    if (!ok) {
                        el.textContent = value;
                    }
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur();
                    return true;
                };
            }
            """
        )
    except Exception:
        return


def _fill_caption_with_keyboard(page: object, text: str) -> bool:
    locator = _find_caption_input(page)
    if locator is None:
        return False
    try:
        locator.click(timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(text)
        page.wait_for_timeout(500)
        return _caption_locator_matches(locator, text)
    except Exception:
        return False


def _find_caption_input(page: object):
    selectors = (
        "textarea[placeholder*='description' i]",
        "textarea[placeholder*='caption' i]",
        "[contenteditable='true'][data-placeholder*='description' i]",
        "[contenteditable='true'][aria-label*='description' i]",
        "[data-e2e='caption-container'] div[contenteditable='true']",
        "div[contenteditable='true']",
        "textarea",
    )
    best = None
    best_score = -1
    for selector in selectors:
        try:
            locators = page.locator(selector)
            count = min(locators.count(), 20)
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if not locator.is_visible(timeout=300):
                        continue
                    score = locator.evaluate(
                        """
                        (el) => {
                            const rect = el.getBoundingClientRect();
                            const attrs = [
                                el.getAttribute('placeholder'),
                                el.getAttribute('aria-label'),
                                el.getAttribute('data-placeholder'),
                                el.closest('[class]')?.innerText,
                                el.parentElement?.innerText
                            ].filter(Boolean).join(' ').toLowerCase();
                            let value = rect.width * rect.height;
                            if (attrs.includes('description') || attrs.includes('caption')) value += 1000000;
                            if (attrs.includes('location')) value -= 1000000;
                            return value;
                        }
                        """
                    )
                    if score > best_score:
                        best = locator
                        best_score = score
                except Exception:
                    continue
        except Exception:
            continue
    return best


def _fill_caption_with_dom(page: object, text: str) -> bool:
    try:
        changed = page.evaluate(
            """
            (value) => {
                const normalize = (raw) => (raw || '').trim().replace(/\\s+/g, ' ');
                const read = (el) => ('value' in el ? el.value : (el.innerText || el.textContent || ''));
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 80 && rect.height > 30 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const score = (el) => {
                    const rect = el.getBoundingClientRect();
                    const attrs = [
                        el.getAttribute('placeholder'),
                        el.getAttribute('aria-label'),
                        el.getAttribute('data-placeholder'),
                        el.closest('[class]')?.innerText,
                        el.parentElement?.innerText
                    ].filter(Boolean).join(' ').toLowerCase();
                    let value = rect.width * rect.height;
                    if (attrs.includes('description') || attrs.includes('caption')) value += 1000000;
                    return value;
                };
                const candidates = Array.from(document.querySelectorAll("textarea, [contenteditable='true']"))
                    .filter(visible)
                    .map((el) => ({ el, score: score(el), rect: el.getBoundingClientRect() }))
                    .sort((a, b) => b.score - a.score);
                if (!candidates.length) return null;
                const best = candidates[0];
                best.el.scrollIntoView({ block: 'center', inline: 'center' });
                window.__setTikTokCaptionExact(best.el, value);
                return normalize(read(best.el)) === normalize(value);
            }
            """,
            text,
        )
    except Exception:
        changed = False
    if not changed:
        return False
    try:
        page.wait_for_timeout(500)
        return _caption_text_is_present(page, text)
    except Exception:
        return False


def _force_caption_exact(page: object, text: str) -> bool:
    try:
        changed = page.evaluate(
            """
            (value) => {
                const normalize = (raw) => (raw || '').trim().replace(/\\s+/g, ' ');
                const target = normalize(value);
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 80 && rect.height > 30 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const read = (el) => ('value' in el ? el.value : (el.innerText || el.textContent || ''));
                const fields = Array.from(document.querySelectorAll("textarea, input, [contenteditable='true']"))
                    .filter(visible);
                const containing = fields.find((el) => normalize(read(el)).includes(target));
                if (containing) return window.__setTikTokCaptionExact(containing, value);
                const largest = fields
                    .map((el) => ({ el, area: el.getBoundingClientRect().width * el.getBoundingClientRect().height }))
                    .sort((a, b) => b.area - a.area)[0]?.el;
                if (!largest) return false;
                return window.__setTikTokCaptionExact(largest, value);
            }
            """,
            text,
        )
    except Exception:
        changed = False
    if not changed:
        return False
    try:
        page.wait_for_timeout(500)
        locator = _find_caption_input(page)
        if locator is not None:
            return _caption_locator_matches(locator, text)
        return _caption_text_is_present(page, text)
    except Exception:
        return False


def _caption_locator_matches(locator, text: str) -> bool:
    target = _normalize_caption_text(text)
    try:
        value = locator.evaluate(
            """
            (el) => ('value' in el ? el.value : (el.innerText || el.textContent || ''))
            """
        )
        return _normalize_caption_text(value) == target
    except Exception:
        return False


def _caption_text_is_present(page: object, text: str) -> bool:
    target = _normalize_caption_text(text)
    if not target:
        return True
    try:
        values = page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 80
                        && rect.height > 25
                        && style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.bottom >= 0
                        && rect.top <= window.innerHeight;
                };
                return Array.from(document.querySelectorAll("textarea, [contenteditable='true']"))
                    .filter(visible)
                .map((el) => ('value' in el ? el.value : (el.innerText || el.textContent || '')))
            }
            """
        )
        for value in values:
            if _normalize_caption_text(value) == target:
                return True
    except Exception:
        pass
    return False


def _normalize_caption_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def _set_visibility(page: object, visibility: str) -> None:
    option_texts = {
        "public": ("Everyone", "Public", "Mọi người", "Moi nguoi"),
        "friends": ("Friends", "Bạn bè", "Ban be"),
        "private": ("Only me", "Private", "Chỉ mình tôi", "Chi minh toi"),
    }.get(visibility or "public", ("Everyone", "Mọi người", "Moi nguoi"))
    trigger_texts = ("Everyone", "Mọi người", "Moi nguoi", "Bạn bè", "Ban be", "Only me", "Chỉ mình tôi")
    try:
        _click_any_text(page, trigger_texts, timeout=3000)
        page.wait_for_timeout(500)
        _click_any_text(page, option_texts, timeout=3000)
    except Exception:
        return


def _set_schedule(page: object, scheduled_at: str) -> None:
    if not scheduled_at:
        return
    try:
        _click_any_text(page, ("Schedule", "Lên lịch", "Len lich"), timeout=3000)
    except Exception:
        return
    value = scheduled_at.replace("T", " ")
    date_part = value.split(" ")[0] if " " in value else value
    time_part = value.split(" ")[1][:5] if " " in value else ""
    inputs = (
        ("input[type='date']", date_part),
        ("input[type='time']", time_part),
    )
    for selector, input_value in inputs:
        if not input_value:
            continue
        try:
            field = page.locator(selector).first
            field.fill(input_value, timeout=3000)
        except Exception:
            continue


def _set_safety_check(page: object, kind: str, enabled: bool) -> bool:
    extra_labels = ("Content check lite",) if kind == "quick" else ()
    label_sets = {
        "copyright": ("Music copyright check", "Kiểm tra bản quyền nhạc", "Kiem tra ban quyen nhac"),
        "quick": ("Quick content check", "Kiểm tra nội dung nhanh", "Kiem tra noi dung nhanh"),
    }
    for label in extra_labels + label_sets.get(kind, ()):
        try:
            control = page.get_by_label(label, exact=False)
            if control.count() > 0:
                checked = control.first.is_checked(timeout=1000)
                if checked != bool(enabled):
                    control.first.click(timeout=2000)
                return True
        except Exception:
            continue
    labels = extra_labels + label_sets.get(kind, ())
    return _set_safety_check_by_dom(page, labels, enabled)


def _set_safety_check_by_dom(page: object, labels: tuple[str, ...], enabled: bool) -> bool:
    try:
        result = page.evaluate(
            """
            ({ labels, enabled }) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                const lowerLabels = labels.map((label) => label.toLowerCase());
                const labelItems = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '') }))
                    .filter((item) => item.text.length <= 220)
                    .filter((item) => lowerLabels.some((label) => item.text.toLowerCase().includes(label)))
                    .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                const labelItem = labelItems[0];
                if (!labelItem) return false;

                let row = labelItem.el;
                for (let depth = 0; row && depth < 7; depth += 1) {
                    const box = row.getBoundingClientRect();
                    if (box.width >= 180 && box.height >= 28 && box.height <= 140) break;
                    row = row.parentElement;
                }
                if (!row) row = labelItem.el;
                const rowBox = row.getBoundingClientRect();
                const candidates = Array.from(document.querySelectorAll('button,[role="switch"],[role="checkbox"],input[type="checkbox"],[aria-checked]'))
                    .filter(visible)
                    .map((el) => {
                        const box = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return {
                            el,
                            box,
                            aria: el.getAttribute('aria-checked'),
                            checked: 'checked' in el ? Boolean(el.checked) : null,
                            bg: style.backgroundColor || '',
                            className: el.className ? String(el.className) : '',
                        };
                    })
                    .filter((item) => {
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerY >= rowBox.top - 12
                            && centerY <= rowBox.bottom + 12
                            && item.box.left >= labelItem.box.left
                            && item.box.width <= 100
                            && item.box.height <= 70;
                    })
                    .sort((a, b) => b.box.left - a.box.left);
                const target = candidates[0];
                if (!target) return false;

                let checked = target.checked;
                if (checked === null && target.aria !== null) checked = target.aria === 'true';
                if (checked === null) {
                    const value = `${target.bg} ${target.className}`.toLowerCase();
                    checked = value.includes('rgb(0, 184')
                        || value.includes('rgb(0, 186')
                        || value.includes('rgb(34, 197')
                        || value.includes('checked')
                        || value.includes('active');
                }
                if (checked === enabled) return true;
                target.el.click();
                return true;
            }
            """,
            {"labels": list(labels), "enabled": bool(enabled)},
        )
        return bool(result)
    except Exception:
        return False


def _set_safety_checks_during_upload(
    page: object,
    copyright_check: bool,
    quick_content_check: bool,
    timeout: int = 8000,
) -> None:
    deadline = time.time() + timeout / 1000
    copyright_done = False
    quick_done = False
    while time.time() < deadline and not (copyright_done and quick_done):
        if not copyright_done:
            copyright_done = _set_safety_check(page, "copyright", copyright_check)
        if not quick_done:
            quick_done = _set_safety_check(page, "quick", quick_content_check)
        if copyright_done and quick_done:
            return
        page.wait_for_timeout(300)


def _wait_for_safety_checks_ready(
    page: object,
    require_copyright: bool = True,
    require_quick: bool = True,
    timeout: int = 180000,
) -> None:
    required = []
    if require_copyright:
        required.append("copyright")
    if require_quick:
        required.append("quick")
    if not required:
        return

    deadline = time.time() + timeout / 1000
    last_state: dict[str, bool] = {}
    while time.time() < deadline:
        last_state = _read_safety_check_results(page)
        if all(last_state.get(kind) for kind in required):
            return
        page.wait_for_timeout(1000)
    missing = ", ".join(kind for kind in required if not last_state.get(kind))
    raise RuntimeError("Safety checks are not ready: %s does not show No issues found." % (missing or "unknown"))


def _read_safety_check_results(page: object) -> dict[str, bool]:
    try:
        result = page.evaluate(
            """
            () => {
                const labels = {
                    copyright: ['Music copyright check', 'Kiem tra ban quyen nhac'],
                    quick: ['Content check lite', 'Quick content check', 'Kiem tra noi dung nhanh'],
                };
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                const read = (el) => norm(
                    ('value' in el && el.value ? el.value : '')
                    || el.innerText
                    || el.textContent
                    || el.getAttribute('aria-label')
                    || ''
                );
                const all = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: read(el) }))
                    .filter((item) => item.text);
                const labelItems = [];
                for (const [kind, names] of Object.entries(labels)) {
                    for (const item of all) {
                        const lower = item.text.toLowerCase();
                        if (item.text.length > 120) continue;
                        if (names.some((name) => lower.includes(name.toLowerCase()))) {
                            labelItems.push({ ...item, kind });
                        }
                    }
                }
                const result = { copyright: false, quick: false };
                for (const kind of Object.keys(labels)) {
                    const label = labelItems
                        .filter((item) => item.kind === kind)
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height))[0];
                    if (!label) continue;
                    const nextLabelTop = labelItems
                        .filter((item) => item.kind !== kind && item.box.top > label.box.top + 4)
                        .map((item) => item.box.top)
                        .sort((a, b) => a - b)[0] || label.box.top + 140;
                    result[kind] = all.some((item) => {
                        if (!/no issues? found/i.test(item.text)) return false;
                        return item.box.top >= label.box.top
                            && item.box.top < nextLabelTop
                            && item.box.left >= Math.max(0, label.box.left - 30);
                    });
                }
                return result;
            }
            """
        )
        return {"copyright": bool(result.get("copyright")), "quick": bool(result.get("quick"))}
    except Exception:
        return {}


def _attach_product(page: object, product_id: str) -> None:
    product_id = (product_id or "").strip()
    if not product_id:
        return
    _click_any_text(
        page,
        (
            "Add product links",
            "Add product link",
            "Add product",
            "Thêm liên kết sản phẩm",
            "Them lien ket san pham",
            "Thêm sản phẩm",
        ),
        timeout=7000,
    )
    page.wait_for_timeout(1000)
    try:
        _click_any_text(page, ("Showcase products", "Showcase product", "Sản phẩm trưng bày"), timeout=4000)
        page.wait_for_timeout(700)
    except Exception:
        pass
    input_locator = _first_visible_locator(
        page,
        (
            "input[placeholder*='Product']",
            "input[placeholder*='product']",
            "input[placeholder*='sản phẩm']",
            "input[type='search']",
            "input",
        ),
    )
    if input_locator is None:
        raise RuntimeError("Could not find product search input.")
    input_locator.fill(product_id, timeout=5000)
    input_locator.press("Enter", timeout=3000)
    page.wait_for_timeout(2000)
    try:
        _click_any_text(page, (product_id,), timeout=3000)
    except Exception:
        pass
    _click_any_text(page, ("Add", "Confirm", "OK", "Thêm", "Xác nhận"), timeout=5000)


def _click_submit(page: object, scheduled: bool) -> None:
    labels = (
        ("Schedule", "Lên lịch", "Len lich")
        if scheduled
        else ("Post", "Đăng", "Dang", "Publish")
    )
    _click_any_button(page, labels, timeout=7000)


def _click_any_button(page: object, texts: tuple[str, ...], timeout: int = 3000) -> None:
    last_error = None
    for text in texts:
        try:
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)).first.click(timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
    _click_any_text(page, texts, timeout=timeout)


def _click_any_text(page: object, texts: tuple[str, ...], timeout: int = 3000) -> None:
    last_error = None
    for text in texts:
        try:
            page.get_by_text(text, exact=False).first.click(timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError("Could not click any of: %s. Last error: %s" % (", ".join(texts), last_error))


def _first_visible_locator(page: object, selectors: tuple[str, ...]):
    for selector in selectors:
        try:
            locators = page.locator(selector)
            count = min(locators.count(), 20)
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if locator.is_visible(timeout=500):
                        return locator
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _set_schedule(page: object, scheduled_at: str) -> None:
    if not scheduled_at:
        return
    scheduled_dt = _parse_schedule_time(scheduled_at)
    _click_schedule_option(page)
    _accept_schedule_save_prompt(page)
    page.wait_for_timeout(800)
    _scroll_schedule_controls_into_view(page)

    _set_schedule_date_picker(page, scheduled_dt)
    _set_schedule_time_picker(page, scheduled_dt)
    _verify_schedule_selection(page, scheduled_dt)


def _parse_schedule_time(value: str) -> datetime:
    normalized = (value or "").strip().replace("T", " ")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeError("Invalid scheduled_at value: %s" % value) from exc


def _click_schedule_option(page: object) -> None:
    labels = ("Schedule", "Len lich")
    for label in labels:
        try:
            radio = page.get_by_label(label, exact=False).first
            radio.check(timeout=3000)
            return
        except Exception:
            pass
    _click_any_text(page, labels, timeout=4000)


def _accept_schedule_save_prompt(page: object) -> None:
    for label in ("Allow", "Confirm", "OK", "Got it", "Cho phep", "Dong y"):
        try:
            button = page.get_by_role("button", name=re.compile(r"^\s*%s\s*$" % re.escape(label), re.I)).first
            if button.is_visible(timeout=1200):
                button.click(timeout=3000)
                page.wait_for_timeout(700)
                return
        except Exception:
            pass


def _scroll_schedule_controls_into_view(page: object) -> None:
    try:
        page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const read = (el) => norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent));
                const values = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                    .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text) || /^\\d{4}-\\d{2}-\\d{2}$/.test(item.text))
                    .sort((a, b) => a.box.top - b.box.top);
                if (values.length) {
                    let target = values[0].el;
                    for (let depth = 0; target && depth < 6; depth += 1) {
                        const box = target.getBoundingClientRect();
                        if (box.width >= 120 && box.height >= 36 && box.height <= 120) {
                            break;
                        }
                        target = target.parentElement;
                    }
                    (target || values[0].el).scrollIntoView({ block: 'center', inline: 'nearest' });
                    return true;
                }
                const labels = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, text: read(el) }))
                    .filter((item) => item.text === 'When to post' || item.text === 'Schedule' || item.text === 'Len lich');
                const target = labels[0]?.el;
                if (target) {
                    target.scrollIntoView({ block: 'center', inline: 'nearest' });
                    return true;
                }
                return false;
            }
            """
        )
        page.wait_for_timeout(500)
    except Exception:
        try:
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(500)
        except Exception:
            pass


def _fill_native_schedule_inputs(page: object, date_part: str, time_part: str) -> bool:
    changed = False
    for selector, input_value in (("input[type='date']", date_part), ("input[type='time']", time_part)):
        try:
            locators = page.locator(selector)
            count = min(locators.count(), 5)
            for index in range(count):
                field = locators.nth(index)
                if not field.is_visible(timeout=500):
                    continue
                field.fill(input_value, timeout=3000)
                changed = True
                break
        except Exception:
            continue
    return changed


def _set_schedule_date_picker(page: object, scheduled_dt: datetime) -> None:
    date_part = scheduled_dt.strftime("%Y-%m-%d")
    current_text = _get_selected_schedule_date_text(page)
    if current_text == date_part:
        return
    last_date = current_text
    for _attempt in range(3):
        if not _click_schedule_date_dropdown(page):
            raise RuntimeError("Could not open schedule date dropdown.")
        page.wait_for_timeout(500)
        _move_calendar_to_month(page, scheduled_dt)
        _click_calendar_day(page, scheduled_dt, timeout=5000)
        page.wait_for_timeout(500)
        last_date = _get_selected_schedule_date_text(page)
        if last_date == date_part:
            return
    raise RuntimeError("Could not set schedule date. Expected %s, got %s." % (date_part, last_date or "<missing date>"))


def _move_calendar_to_month(page: object, scheduled_dt: datetime) -> None:
    target_year = str(scheduled_dt.year)
    target_month = scheduled_dt.strftime("%B")
    for _attempt in range(24):
        try:
            body = page.locator("body").inner_text(timeout=1000)
            if target_year in body and target_month in body:
                return
        except Exception:
            return
        try:
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(200)
        except Exception:
            return


def _set_schedule_time_picker(page: object, scheduled_dt: datetime) -> None:
    time_part = scheduled_dt.strftime("%H:%M")
    hour_text = scheduled_dt.strftime("%H")
    minute_text = scheduled_dt.strftime("%M")
    if _get_selected_schedule_time_text(page) == time_part:
        return
    actual = ""
    for _attempt in range(5):
        if not _time_picker_is_open(page):
            if not _click_schedule_time_dropdown(page):
                raise RuntimeError("Could not open schedule time dropdown.")
            page.wait_for_timeout(500)

        for _settle_attempt in range(3):
            _click_time_picker_value(page, hour_text, column="hour", timeout=20000)
            page.wait_for_timeout(250)
            _click_time_picker_value(page, minute_text, column="minute", timeout=20000)
            page.wait_for_timeout(250)

            selection = _get_time_picker_selection(page)
            if selection.get("hour") == hour_text and selection.get("minute") == minute_text:
                _dismiss_time_picker_dropdown(page)
                break
            page.wait_for_timeout(250)
        else:
            continue

        page.wait_for_timeout(800)
        actual = _get_selected_schedule_time_text(page)
        if actual == time_part:
            return
    raise RuntimeError("Could not set schedule time. Expected %s, got %s." % (time_part, actual or "<missing time>"))


def _get_selected_schedule_time_text(page: object) -> str:
    try:
        return str(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const read = (el) => norm(
                        (('value' in el && el.value) ? el.value : '')
                        || el.innerText
                        || el.textContent
                        || el.getAttribute('aria-label')
                        || el.getAttribute('title')
                        || ''
                    );
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const labels = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                        .filter((item) => item.text === 'When to post' || item.text === 'Thoi diem dang')
                        .sort((a, b) => a.box.top - b.box.top);
                    const labelBox = labels[0]?.box;
                    const candidates = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                        .filter((item) => /\\b\\d{2}:\\d{2}\\b/.test(item.text))
                        .filter((item) => {
                            if (!labelBox) return true;
                            return item.box.top >= labelBox.top && item.box.top <= labelBox.bottom + 180;
                        })
                        .filter((item) => item.box.left < window.innerWidth * 0.75)
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                    for (const candidate of candidates) {
                        let node = candidate.el;
                        for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                            if (!visible(node)) continue;
                            const box = node.getBoundingClientRect();
                            const text = read(node);
                            const match = text.match(/\\b\\d{2}:\\d{2}\\b/);
                            if (!match) continue;
                            if (box.width >= 80 && box.width <= 380 && box.height >= 24 && box.height <= 100) {
                                return match[0];
                            }
                        }
                        const match = candidate.text.match(/\\b\\d{2}:\\d{2}\\b/);
                        if (match) return match[0];
                    }
                    return '';
                }
                """
            )
        )
    except Exception:
        return ""


def _get_selected_schedule_date_text(page: object) -> str:
    try:
        return str(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const read = (el) => norm(
                        (('value' in el && el.value) ? el.value : '')
                        || el.innerText
                        || el.textContent
                        || el.getAttribute('aria-label')
                        || el.getAttribute('title')
                        || ''
                    );
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const labels = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                        .filter((item) => item.text === 'When to post' || item.text === 'Thoi diem dang')
                        .sort((a, b) => a.box.top - b.box.top);
                    const labelBox = labels[0]?.box;
                    const candidates = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                        .filter((item) => /\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(item.text))
                        .filter((item) => {
                            if (!labelBox) return true;
                            return item.box.top >= labelBox.top && item.box.top <= labelBox.bottom + 180;
                        })
                        .filter((item) => item.box.left < window.innerWidth * 0.75)
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                    for (const candidate of candidates) {
                        let node = candidate.el;
                        for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                            if (!visible(node)) continue;
                            const box = node.getBoundingClientRect();
                            const text = read(node);
                            const match = text.match(/\\b\\d{4}-\\d{2}-\\d{2}\\b/);
                            if (!match) continue;
                            if (box.width >= 100 && box.width <= 420 && box.height >= 24 && box.height <= 100) {
                                return match[0];
                            }
                        }
                        const match = candidate.text.match(/\\b\\d{4}-\\d{2}-\\d{2}\\b/);
                        if (match) return match[0];
                    }
                    return '';
                }
                """
            )
        )
    except Exception:
        return ""


def _click_schedule_time_dropdown(page: object) -> bool:
    _scroll_schedule_controls_into_view(page)
    return _click_schedule_gray_box(page, "time")


def _click_schedule_date_dropdown(page: object) -> bool:
    _scroll_schedule_controls_into_view(page)
    return _click_schedule_gray_box(page, "date")


def _click_schedule_gray_box(page: object, kind: str) -> bool:
    pattern = r"^\d{2}:\d{2}$" if kind == "time" else r"^\d{4}-\d{2}-\d{2}$"
    try:
        point = page.evaluate(
            """
            ({ kind, patternText }) => {
                const pattern = new RegExp(patternText);
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const read = (el) => norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent));
                const valueNodes = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                    .filter((item) => pattern.test(item.text))
                    .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));

                for (const item of valueNodes) {
                    let node = item.el;
                    for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                        if (!visible(node)) continue;
                        const style = window.getComputedStyle(node);
                        const box = node.getBoundingClientRect();
                        const text = read(node);
                        const bg = style.backgroundColor || '';
                        const looksGreyBox = (
                            box.width >= 120
                            && box.width <= 360
                            && box.height >= 36
                            && box.height <= 80
                            && pattern.test(text)
                        );
                        if (looksGreyBox) {
                            return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
                        }
                    }
                }

                const greyBoxes = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => {
                        const box = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return { el, box, text: read(el), bg: style.backgroundColor || '' };
                    })
                    .filter((item) => item.box.width >= 120 && item.box.width <= 360)
                    .filter((item) => item.box.height >= 36 && item.box.height <= 80)
                    .filter((item) => {
                        if (kind === 'time') return /\\d{2}:\\d{2}/.test(item.text);
                        return /\\d{4}-\\d{2}-\\d{2}/.test(item.text);
                    })
                    .sort((a, b) => a.box.top - b.box.top || a.box.left - b.box.left);
                const target = greyBoxes[0];
                if (target) {
                    return { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 };
                }

                const scheduleLabel = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                    .filter((item) => item.text === 'Schedule' || item.text === 'Len lich')
                    .sort((a, b) => b.box.top - a.box.top)[0];
                if (scheduleLabel) {
                    const yMin = scheduleLabel.box.bottom;
                    const yMax = scheduleLabel.box.bottom + 120;
                    const rowBoxes = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => {
                            const box = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return { el, box, text: read(el), bg: style.backgroundColor || '' };
                        })
                        .filter((item) => item.box.top >= yMin && item.box.top <= yMax)
                        .filter((item) => item.box.width >= 120 && item.box.width <= 360)
                        .filter((item) => item.box.height >= 36 && item.box.height <= 80)
                        .filter((item) => /\\d{2}:\\d{2}|\\d{4}-\\d{2}-\\d{2}/.test(item.text))
                        .sort((a, b) => a.box.left - b.box.left);
                    const selected = kind === 'time'
                        ? rowBoxes.find((item) => /\\d{2}:\\d{2}/.test(item.text)) || rowBoxes[0]
                        : rowBoxes.find((item) => /\\d{4}-\\d{2}-\\d{2}/.test(item.text)) || rowBoxes[1];
                    if (selected) {
                        return { x: selected.box.left + selected.box.width / 2, y: selected.box.top + selected.box.height / 2 };
                    }
                }

                const fallback = valueNodes[0];
                if (!fallback) return null;
                return { x: fallback.box.left + fallback.box.width / 2, y: fallback.box.top + fallback.box.height / 2 };
            }
            """,
            {"kind": kind, "patternText": pattern},
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _click_schedule_value_box(page: object, pattern: str) -> bool:
    try:
        point = page.evaluate(
            """
            (patternText) => {
                const pattern = new RegExp(patternText);
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const direct = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent) }))
                    .filter((item) => pattern.test(item.text))
                    .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                for (const item of direct) {
                    let node = item.el;
                    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
                        if (!visible(node)) continue;
                        const box = node.getBoundingClientRect();
                        const text = norm(node.innerText || node.textContent);
                        if (!pattern.test(text)) continue;
                        if (box.width >= 90 && box.width <= 360 && box.height >= 28 && box.height <= 90) {
                            return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
                        }
                    }
                    const box = item.box;
                    if (box.width > 0 && box.height > 0) {
                        return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
                    }
                }
                return null;
            }
            """,
            pattern,
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _click_time_picker_value(page: object, text: str, column: str, timeout: int = 5000) -> None:
    deadline = time.time() + timeout / 1000
    target_value = int(text)
    min_value = 0
    max_value = 23 if column == "hour" else 55
    direction = 1
    while time.time() < deadline:
        state = _get_time_picker_column_state(page, column, text)
        if state:
            selected = state.get("selected") or ""
            values = state.get("values") or []
            if selected == text:
                return
            selected_value = int(selected) if selected.isdigit() else None
            numeric_values = [int(value) for value in values if str(value).isdigit()]
            if selected_value is not None:
                direction = _time_picker_scroll_direction(selected_value, target_value, column)
            elif numeric_values:
                if min_value in numeric_values and target_value not in numeric_values:
                    direction = 1
                elif max_value in numeric_values and target_value not in numeric_values:
                    direction = -1
                elif target_value < min(numeric_values):
                    direction = -1
                elif target_value > max(numeric_values):
                    direction = 1
        _scroll_time_picker_column(page, column, direction)
        page.wait_for_timeout(250)
    raise RuntimeError("Could not select schedule %s value %s." % (column, text))


def _time_picker_scroll_direction(selected_value: int, target_value: int, column: str) -> int:
    if target_value < selected_value:
        return -1
    if target_value > selected_value:
        return 1
    return 1


def _dismiss_time_picker_dropdown(page: object) -> None:
    try:
        point = page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                const timeBox = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent)) }))
                    .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                    .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height))[0]?.box || null;
                if (timeBox) {
                    const x = Math.max(20, Math.min(window.innerWidth - 20, timeBox.left + 24));
                    const belowY = timeBox.bottom + 44;
                    const aboveY = timeBox.top - 24;
                    const y = belowY < window.innerHeight - 20 ? belowY : Math.max(20, aboveY);
                    return { x, y };
                }
                return { x: window.innerWidth - 24, y: 24 };
            }
            """
        )
        page.mouse.click(point["x"], point["y"])
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass


def _time_picker_is_open(page: object) -> bool:
    hour_values = _get_visible_time_column_values(page, "hour")
    minute_values = _get_visible_time_column_values(page, "minute")
    return len(hour_values) >= 3 and len(minute_values) >= 3


def _get_time_picker_selection(page: object) -> dict[str, str]:
    hour_state = _get_time_picker_column_state(page, "hour", "")
    minute_state = _get_time_picker_column_state(page, "minute", "")
    return {
        "hour": str((hour_state or {}).get("selected") or ""),
        "minute": str((minute_state or {}).get("selected") or ""),
    }


def _get_time_picker_column_state(page: object, column: str, target_text: str) -> dict | None:
    try:
        return page.evaluate(
            """
            ({ column, targetText }) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const read = (el) => norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent));
                const findTimeBox = () => {
                    const items = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: read(el) }))
                        .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                    return items[0]?.box || null;
                };
                const timeBox = findTimeBox();
                const numeric = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '') }))
                    .filter((item) => /^\\d{2}$/.test(item.text))
                    .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    });
                if (!numeric.length) return null;
                const middle = timeBox ? (timeBox.left + timeBox.right) / 2 : (
                    Math.min(...numeric.map((item) => item.box.left)) + Math.max(...numeric.map((item) => item.box.right))
                ) / 2;
                const columnItems = numeric
                    .filter((item) => {
                        const center = (item.box.left + item.box.right) / 2;
                        return column === 'hour' ? center < middle : center >= middle;
                    })
                    .sort((a, b) => a.box.top - b.box.top);
                if (!columnItems.length) return null;

                const colonItems = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '') }))
                    .filter((item) => item.text === ':' || item.text === '：')
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    })
                    .sort((a, b) => Math.abs(((a.box.top + a.box.bottom) / 2) - (timeBox ? ((timeBox.top + timeBox.bottom) / 2) : ((a.box.top + a.box.bottom) / 2))));
                const pickerTop = Math.min(...columnItems.map((item) => item.box.top));
                const pickerBottom = Math.max(...columnItems.map((item) => item.box.bottom));
                const selectedY = colonItems[0]
                    ? (colonItems[0].box.top + colonItems[0].box.bottom) / 2
                    : (pickerTop + pickerBottom) / 2;
                const selected = columnItems
                    .slice()
                    .sort((a, b) => Math.abs(((a.box.top + a.box.bottom) / 2) - selectedY) - Math.abs(((b.box.top + b.box.bottom) / 2) - selectedY))[0];
                const target = columnItems.find((item) => item.text === targetText) || null;
                return {
                    values: columnItems.map((item) => item.text),
                    selected: selected ? selected.text : '',
                    selectedY,
                    targetPoint: target ? { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 } : null,
                };
            }
            """,
            {"column": column, "targetText": target_text},
        )
    except Exception:
        return None


def _click_center_time_picker_value(page: object, column: str) -> bool:
    try:
        point = page.evaluate(
            """
            (column) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const timeBoxes = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent)) }))
                    .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                    .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                const timeBox = timeBoxes[0]?.box || null;
                const numeric = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: (el.innerText || el.textContent || '').trim() }))
                    .filter((item) => /^\\d{2}$/.test(item.text))
                    .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    });
                if (!numeric.length) return null;
                const middle = timeBox ? (timeBox.left + timeBox.right) / 2 : (
                    Math.min(...numeric.map((item) => item.box.left)) + Math.max(...numeric.map((item) => item.box.right))
                ) / 2;
                const columnItems = numeric
                    .filter((item) => {
                        const center = (item.box.left + item.box.right) / 2;
                        return column === 'hour' ? center < middle : center >= middle;
                    })
                    .sort((a, b) => a.box.top - b.box.top);
                const target = columnItems[Math.floor(columnItems.length / 2)];
                if (!target) return null;
                return { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 };
            }
            """,
            column,
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _get_visible_time_column_values(page: object, column: str) -> list[str]:
    try:
        values = page.evaluate(
            """
            (column) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const findTimeBox = () => {
                    const items = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent)) }))
                        .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                    for (const item of items) {
                        let node = item.el;
                        for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                            if (!visible(node)) continue;
                            const box = node.getBoundingClientRect();
                            const text = norm(('value' in node && node.value) ? node.value : (node.innerText || node.textContent));
                            if (/^\\d{2}:\\d{2}$/.test(text) && box.width >= 100 && box.width <= 380 && box.height >= 28 && box.height <= 100) {
                                return box;
                            }
                        }
                    }
                    return items[0]?.box || null;
                };
                const timeBox = findTimeBox();
                const numeric = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: (el.innerText || el.textContent || '').trim() }))
                    .filter((item) => /^\\d{2}$/.test(item.text))
                    .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    });
                if (!numeric.length) return [];
                const middle = timeBox ? (timeBox.left + timeBox.right) / 2 : (
                    Math.min(...numeric.map((item) => item.box.left)) + Math.max(...numeric.map((item) => item.box.right))
                ) / 2;
                return numeric
                    .filter((item) => {
                        const center = (item.box.left + item.box.right) / 2;
                        return column === 'hour' ? center < middle : center >= middle;
                    })
                    .sort((a, b) => a.box.top - b.box.top)
                    .map((item) => item.text);
            }
            """,
            column,
        )
        return [str(value) for value in values]
    except Exception:
        return []


def _click_visible_time_value(page: object, text: str, column: str) -> bool:
    try:
        point = page.evaluate(
            """
            ({ value, column }) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                const findTimeBox = () => {
                    const items = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent)) }))
                        .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                        .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                    for (const item of items) {
                        let node = item.el;
                        for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                            if (!visible(node)) continue;
                            const box = node.getBoundingClientRect();
                            const text = norm(('value' in node && node.value) ? node.value : (node.innerText || node.textContent));
                            if (/^\\d{2}:\\d{2}$/.test(text) && box.width >= 100 && box.width <= 380 && box.height >= 28 && box.height <= 100) {
                                return box;
                            }
                        }
                    }
                    return items[0]?.box || null;
                };
                const timeBox = findTimeBox();
                const numbers = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ el, box: el.getBoundingClientRect(), text: (el.innerText || el.textContent || '').trim() }))
                    .filter((item) => item.text === value && /^\\d{2}$/.test(item.text))
                    .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    });
                if (!numbers.length) return null;
                const all = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((el) => ({ box: el.getBoundingClientRect(), text: (el.innerText || el.textContent || '').trim() }))
                    .filter((item) => /^\\d{2}$/.test(item.text))
                    .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                    .filter((item) => {
                        if (!timeBox) return true;
                        const centerX = (item.box.left + item.box.right) / 2;
                        const centerY = (item.box.top + item.box.bottom) / 2;
                        return centerX >= timeBox.left - 30
                            && centerX <= timeBox.right + 30
                            && centerY >= timeBox.top - 420
                            && centerY <= timeBox.bottom + 420;
                    });
                const middle = timeBox ? (timeBox.left + timeBox.right) / 2 : (
                    Math.min(...all.map((item) => item.box.left)) + Math.max(...all.map((item) => item.box.right))
                ) / 2;
                const filtered = numbers.filter((item) => {
                    const center = (item.box.left + item.box.right) / 2;
                    return column === 'hour' ? center < middle : center >= middle;
                });
                const target = filtered[0];
                if (!target) return null;
                return { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 };
            }
            """,
            {"value": text, "column": column},
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _scroll_time_picker_column(page: object, column: str, direction: int) -> bool:
    try:
        point = page.evaluate(
                """
                (column) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                    const findTimeBox = () => {
                        const items = Array.from(document.querySelectorAll('body *'))
                            .filter(visible)
                            .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(('value' in el && el.value) ? el.value : (el.innerText || el.textContent)) }))
                            .filter((item) => /^\\d{2}:\\d{2}$/.test(item.text))
                            .sort((a, b) => (a.box.width * a.box.height) - (b.box.width * b.box.height));
                        for (const item of items) {
                            let node = item.el;
                            for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                                if (!visible(node)) continue;
                                const box = node.getBoundingClientRect();
                                const text = norm(('value' in node && node.value) ? node.value : (node.innerText || node.textContent));
                                if (/^\\d{2}:\\d{2}$/.test(text) && box.width >= 100 && box.width <= 380 && box.height >= 28 && box.height <= 100) {
                                    return box;
                                }
                            }
                        }
                        return items[0]?.box || null;
                    };
                    const timeBox = findTimeBox();
                    const numeric = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: (el.innerText || el.textContent || '').trim() }))
                        .filter((item) => /^\\d{2}$/.test(item.text))
                        .filter((item) => item.box.width <= 90 && item.box.height <= 70)
                        .filter((item) => {
                            if (!timeBox) return true;
                            const centerX = (item.box.left + item.box.right) / 2;
                            const centerY = (item.box.top + item.box.bottom) / 2;
                            return centerX >= timeBox.left - 30
                                && centerX <= timeBox.right + 30
                                && centerY >= timeBox.top - 420
                                && centerY <= timeBox.bottom + 420;
                        });
                    if (!numeric.length) return false;
                    const middle = timeBox ? (timeBox.left + timeBox.right) / 2 : (
                        Math.min(...numeric.map((item) => item.box.left)) + Math.max(...numeric.map((item) => item.box.right))
                    ) / 2;
                    const targetItems = numeric.filter((item) => {
                        const center = (item.box.left + item.box.right) / 2;
                        return column === 'hour' ? center < middle : center >= middle;
                    });
                    if (!targetItems.length) return null;
                    const colonItems = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({ box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '') }))
                        .filter((item) => item.text === ':' || item.text === '：')
                        .filter((item) => {
                            if (!timeBox) return true;
                            const centerX = (item.box.left + item.box.right) / 2;
                            const centerY = (item.box.top + item.box.bottom) / 2;
                            return centerX >= timeBox.left - 30
                                && centerX <= timeBox.right + 30
                                && centerY >= timeBox.top - 420
                                && centerY <= timeBox.bottom + 420;
                        })
                        .sort((a, b) => Math.abs(((a.box.top + a.box.bottom) / 2) - (timeBox ? ((timeBox.top + timeBox.bottom) / 2) : ((a.box.top + a.box.bottom) / 2))));
                    const pickerTop = Math.min(...targetItems.map((item) => item.box.top));
                    const pickerBottom = Math.max(...targetItems.map((item) => item.box.bottom));
                    const selectedY = colonItems[0]
                        ? (colonItems[0].box.top + colonItems[0].box.bottom) / 2
                        : (pickerTop + pickerBottom) / 2;
                    const x = targetItems
                        .map((item) => (item.box.left + item.box.right) / 2)
                        .sort((a, b) => a - b)[Math.floor(targetItems.length / 2)];
                    return {
                        x,
                        y: Math.max(10, Math.min(window.innerHeight - 10, selectedY)),
                    };
                }
                """,
                column,
            )
        if not point:
            return False
        page.mouse.move(point["x"], point["y"])
        page.mouse.wheel(0, direction * 420)
        return True
    except Exception:
        return False


def _verify_schedule_selection(page: object, scheduled_dt: datetime) -> None:
    expected_time = scheduled_dt.strftime("%H:%M")
    expected_date = scheduled_dt.strftime("%Y-%m-%d")
    _scroll_schedule_controls_into_view(page)
    deadline = time.time() + 4
    last_time = ""
    last_date = ""
    while time.time() < deadline:
        last_time = _get_selected_schedule_time_text(page)
        last_date = _get_selected_schedule_date_text(page)
        if last_time == expected_time and last_date == expected_date:
            return
        page.wait_for_timeout(300)
    raise RuntimeError(
        "Schedule value mismatch. Expected %s %s, got %s %s."
        % (expected_date, expected_time, last_date or "<missing date>", last_time or "<missing time>")
    )


def _click_calendar_day(page: object, scheduled_dt: datetime, timeout: int = 3000) -> None:
    day_text = str(scheduled_dt.day)
    padded_day_text = scheduled_dt.strftime("%d")
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        try:
            point = page.evaluate(
                """
                ({ day, paddedDay }) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const norm = (raw) => (raw || '').replace(/\\s+/g, ' ').trim();
                    const textMatches = (value) => {
                        const text = norm(value);
                        return text === day || text === paddedDay || new RegExp(`^${day}\\\\b`).test(text);
                    };
                    const calendarBoxes = Array.from(document.querySelectorAll('[role="dialog"],[role="grid"],[class*="calendar" i],[class*="picker" i],[class*="popover" i],[class*="dropdown" i]'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect() }))
                        .filter((item) => item.box.height >= 160 && item.box.width >= 180)
                        .sort((a, b) => b.box.top - a.box.top);
                    const calendarBox = calendarBoxes[0]?.box || null;
                    const insideCalendar = (box) => {
                        if (!calendarBox) return box.top > window.innerHeight * 0.18;
                        const centerX = (box.left + box.right) / 2;
                        const centerY = (box.top + box.bottom) / 2;
                        return centerX >= calendarBox.left
                            && centerX <= calendarBox.right
                            && centerY >= calendarBox.top
                            && centerY <= calendarBox.bottom;
                    };
                    const clickBoxFor = (el, textBox) => {
                        let target = el;
                        for (let depth = 0; target && depth < 6; depth += 1) {
                            const tag = target.tagName.toLowerCase();
                            const role = target.getAttribute('role') || '';
                            const box = target.getBoundingClientRect();
                            const clickable = tag === 'button' || tag === 'td' || role === 'button' || role === 'gridcell';
                            if (clickable && box.width >= textBox.width && box.height >= textBox.height && box.width <= 120 && box.height <= 120) {
                                return box;
                            }
                            target = target.parentElement;
                        }
                        return textBox;
                    };

                    const textNodeCandidates = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const node = walker.currentNode;
                        if (!textMatches(node.nodeValue)) continue;
                        const parent = node.parentElement;
                        if (!parent || !visible(parent)) continue;
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        const rects = Array.from(range.getClientRects()).filter((box) => box.width > 0 && box.height > 0);
                        range.detach();
                        for (const textBox of rects) {
                            if (textBox.width > 90 || textBox.height > 90 || !insideCalendar(textBox)) continue;
                            const box = clickBoxFor(parent, textBox);
                            textNodeCandidates.push({ box });
                        }
                    }
                    if (textNodeCandidates.length) {
                        const target = textNodeCandidates.sort((a, b) => (b.box.width * b.box.height) - (a.box.width * a.box.height))[0];
                        return { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 };
                    }

                    const dayTexts = Array.from(document.querySelectorAll('button,[role="button"],[role="gridcell"],td,div,span'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent || '') }))
                        .filter((item) => textMatches(item.text) && item.box.width <= 90 && item.box.height <= 90)
                        .filter((item) => insideCalendar(item.box))
                        .map((item) => {
                            return { box: clickBoxFor(item.el, item.box) };
                        })
                        .sort((a, b) => (b.box.width * b.box.height) - (a.box.width * a.box.height));
                    const target = dayTexts[0];
                    if (!target) return null;
                    return { x: target.box.left + target.box.width / 2, y: target.box.top + target.box.height / 2 };
                }
                """,
                {"day": day_text, "paddedDay": padded_day_text},
            )
            if point:
                page.mouse.click(point["x"], point["y"])
                return
        except Exception:
            pass
        page.wait_for_timeout(200)
    raise RuntimeError("Could not select schedule day %s." % day_text)


def _click_visible_exact_text(page: object, text: str, timeout: int = 3000) -> None:
    deadline = time.time() + timeout / 1000
    last_error = None
    while time.time() < deadline:
        try:
            locators = page.get_by_text(text, exact=True)
            count = min(locators.count(), 30)
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if locator.is_visible(timeout=300):
                        locator.click(timeout=1000)
                        return
                except Exception as exc:
                    last_error = exc
                    continue
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(200)
    raise RuntimeError("Could not click visible text %s. Last error: %s" % (text, last_error))


def _attach_product(page: object, product_id: str) -> None:
    product_id = (product_id or "").strip()
    if not product_id:
        return

    _open_product_link_modal(page)
    _click_product_modal_next(page, wait_for="search", product_id=product_id)
    _search_showcase_product(page, product_id)
    _select_showcase_product(page, product_id)
    _click_product_modal_next(page, wait_for="confirm", product_id=product_id)
    _click_product_modal_add(page, product_id)
    _wait_for_product_attached(page, product_id)


def _open_product_link_modal(page: object) -> None:
    if _product_link_modal_is_open(page):
        return

    for attempt in range(3):
        page.wait_for_timeout(500)
        if attempt > 0:
            try:
                page.mouse.wheel(0, -500 if attempt == 2 else 700)
                page.wait_for_timeout(400)
            except Exception:
                pass

        for clicker in (
            _click_add_link_button_near_section,
            _click_plus_add_text,
            _click_add_link_button_by_coordinates,
            _click_add_link_button_relative_to_heading,
        ):
            clicker(page)
            page.wait_for_timeout(700)
            if _product_link_modal_is_open(page):
                return

    raise RuntimeError("Could not click + Add button to open product link dialog.")


def _product_link_modal_is_open(page: object) -> bool:
    return (
        _page_has_visible_text(page, "Link type")
        or _page_has_visible_text(page, "Add product links")
        or _find_product_search_input(page) is not None
    )


def _click_add_link_button_near_section(page: object) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const texts = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .filter((el) => ['Add link', 'Them link'].includes(norm(el.textContent)));
                    for (const heading of texts) {
                        heading.scrollIntoView({ block: 'center', inline: 'nearest' });
                        let root = heading;
                        for (let depth = 0; root && depth < 6; depth += 1, root = root.parentElement) {
                            const controls = Array.from(root.querySelectorAll('button,[role="button"],div,span'))
                                .filter(visible)
                                .filter((el) => {
                                    const text = norm(el.innerText || el.textContent);
                                    return text === '+ Add' || text === 'Add' || text === '+ Them' || text === 'Them';
                                });
                            const target = controls.find((el) => {
                                const box = el.getBoundingClientRect();
                                return box.width >= 80 && box.height >= 25;
                            }) || controls[0];
                            if (target) {
                                target.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


def _click_plus_add_text(page: object) -> bool:
    patterns = (
        re.compile(r"^\s*\+\s*Add\s*$", re.I),
        re.compile(r"^\s*Add\s*$", re.I),
        re.compile(r"^\s*\+\s*Them\s*$", re.I),
        re.compile(r"^\s*Them\s*$", re.I),
    )
    for pattern in patterns:
        try:
            locators = page.get_by_text(pattern)
            count = min(locators.count(), 20)
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if not locator.is_visible(timeout=300):
                        continue
                    locator.scroll_into_view_if_needed(timeout=1000)
                    box = locator.bounding_box(timeout=1000)
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    else:
                        locator.click(timeout=1000, force=True)
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _click_add_link_button_by_coordinates(page: object) -> bool:
    try:
        point = page.evaluate(
            """
            () => {
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const headings = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .filter((el) => ['Add link', 'Them link'].includes(norm(el.textContent)));
                for (const heading of headings) {
                    heading.scrollIntoView({ block: 'center', inline: 'nearest' });
                    const headingBox = heading.getBoundingClientRect();
                    const controls = Array.from(document.querySelectorAll('button,[role="button"],div,span'))
                        .filter(visible)
                        .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent) }))
                        .filter((item) => item.text === '+ Add' || item.text === 'Add' || item.text === '+ Them' || item.text === 'Them')
                        .filter((item) => item.box.top > headingBox.bottom - 20 && item.box.top < headingBox.bottom + 160);
                    const target = controls.find((item) => item.box.width >= 80 && item.box.height >= 25) || controls[0];
                    if (target) {
                        return {
                            x: target.box.left + target.box.width / 2,
                            y: target.box.top + target.box.height / 2
                        };
                    }
                }
                return null;
            }
            """
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _click_add_link_button_relative_to_heading(page: object) -> bool:
    try:
        point = page.evaluate(
            """
            () => {
                const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0
                        && box.bottom >= 0
                        && box.top <= window.innerHeight;
                };
                const heading = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .find((el) => ['Add link', 'Them link'].includes(norm(el.textContent)));
                if (!heading) {
                    return null;
                }
                heading.scrollIntoView({ block: 'center', inline: 'nearest' });
                const box = heading.getBoundingClientRect();
                return { x: box.left + 180, y: box.bottom + 48 };
            }
            """
        )
        if not point:
            return False
        page.mouse.click(point["x"], point["y"])
        return True
    except Exception:
        return False


def _click_product_modal_next(page: object, wait_for: str = "", product_id: str = "") -> None:
    _click_visible_button_exact(page, "Next", timeout=8000)
    if wait_for == "search":
        _wait_until(
            page,
            lambda: _find_product_search_input(page) is not None,
            timeout=12000,
            error="Product search input did not appear after clicking Next.",
        )
    elif wait_for == "confirm":
        _wait_until(
            page,
            lambda: _product_confirmation_ready(page, product_id),
            timeout=15000,
            error="Product confirmation dialog did not become ready after clicking Next.",
        )
    else:
        page.wait_for_timeout(900)


def _search_showcase_product(page: object, product_id: str) -> None:
    input_locator = _find_product_search_input(page)
    if input_locator is None:
        raise RuntimeError("Could not find product search input.")

    input_locator.fill(product_id, timeout=5000)
    page.wait_for_timeout(300)
    if not _click_product_search_icon(page, product_id):
        try:
            input_locator.press("Enter", timeout=3000)
        except Exception:
            pass

    _wait_until(
        page,
        lambda: _page_has_visible_text(page, product_id),
        timeout=12000,
        error="Product ID was not found after search: %s" % product_id,
    )
    page.wait_for_timeout(500)


def _find_product_search_input(page: object):
    return _first_visible_locator(
        page,
        (
            "input[placeholder='Search products']",
            "input[placeholder*='Search products']",
            "input[placeholder*='search products']",
            "input[placeholder*='Product']",
            "input[placeholder*='product']",
        ),
    )


def _click_product_search_icon(page: object, product_id: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (productId) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const inputs = Array.from(document.querySelectorAll('input'))
                        .filter(visible)
                        .filter((el) => (el.value || '').trim() === productId);
                    for (const input of inputs) {
                        let root = input.parentElement;
                        for (let depth = 0; root && depth < 5; depth += 1, root = root.parentElement) {
                            const inputBox = input.getBoundingClientRect();
                            const controls = Array.from(root.querySelectorAll('button,[role="button"],svg'))
                                .filter(visible)
                                .filter((el) => {
                                    const box = el.getBoundingClientRect();
                                    return box.left > inputBox.left
                                        && box.left < inputBox.right + 80
                                        && Math.abs((box.top + box.bottom) / 2 - (inputBox.top + inputBox.bottom) / 2) < 35;
                                });
                            const target = controls[controls.length - 1];
                            if (target) {
                                target.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                product_id,
            )
        )
    except Exception:
        return False


def _select_showcase_product(page: object, product_id: str) -> None:
    selected = False
    deadline = time.time() + 10
    while time.time() < deadline and not selected:
        try:
            selected = bool(
                page.evaluate(
                    """
                    (productId) => {
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            const box = el.getBoundingClientRect();
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && box.width > 0
                                && box.height > 0;
                        };
                        const matches = Array.from(document.querySelectorAll('body *'))
                            .filter(visible)
                            .filter((el) => (el.textContent || '').includes(productId));
                        for (const match of matches) {
                            let row = match.closest('tr,[role="row"]');
                            if (!row) {
                                row = match;
                                for (let depth = 0; row && depth < 6; depth += 1) {
                                    const text = row.textContent || '';
                                    const box = row.getBoundingClientRect();
                                    if (text.includes(productId) && box.width > 400 && box.height > 35) {
                                        break;
                                    }
                                    row = row.parentElement;
                                }
                            }
                            if (!row || !visible(row)) {
                                continue;
                            }
                            const controls = Array.from(row.querySelectorAll('input[type="radio"],input[type="checkbox"],[role="radio"],[role="checkbox"]'))
                                .filter(visible);
                            if (controls.length) {
                                controls[0].click();
                                return true;
                            }
                            const box = row.getBoundingClientRect();
                            const x = box.left + Math.min(35, Math.max(15, box.width * 0.04));
                            const y = box.top + box.height / 2;
                            const target = document.elementFromPoint(x, y);
                            if (target) {
                                target.click();
                                return true;
                            }
                        }
                        return false;
                    }
                    """,
                    product_id,
                )
            )
        except Exception:
            selected = False
        if not selected:
            page.wait_for_timeout(300)
    if not selected:
        raise RuntimeError("Could not select product row for ID: %s" % product_id)
    page.wait_for_timeout(600)


def _click_product_modal_add(page: object, product_id: str) -> None:
    _wait_until(
        page,
        lambda: _product_confirmation_ready(page, product_id),
        timeout=15000,
        error="Could not open product name confirmation dialog.",
    )
    if not _click_product_confirmation_add_button(page):
        _click_visible_button_exact(page, "Add", timeout=12000)
    if not _wait_for_product_confirmation_invalid_name_error(page, timeout=2500):
        return
    _set_product_confirmation_name(page, "Mua ở đây")
    if not _click_product_confirmation_add_button(page):
        _click_visible_button_exact(page, "Add", timeout=12000)


def _wait_for_product_confirmation_invalid_name_error(page: object, timeout: int = 2500) -> bool:
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if _product_confirmation_invalid_name_error_visible(page):
            return True
        if not _page_has_visible_text(page, "Product name"):
            return False
        page.wait_for_timeout(200)
    return _product_confirmation_invalid_name_error_visible(page)


def _product_confirmation_invalid_name_error_visible(page: object) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    return Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .some((el) => {
                            const text = norm(el.innerText || el.textContent);
                            return text.includes('remove invalid characters')
                                || text.includes('invalid characters');
                        });
                }
                """
            )
        )
    except Exception:
        return False


def _click_visible_button_exact(page: object, text: str, timeout: int = 3000) -> None:
    deadline = time.time() + timeout / 1000
    last_error = None
    while time.time() < deadline:
        try:
            if page.evaluate(
                """
                (label) => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const controls = Array.from(document.querySelectorAll('button,[role="button"]'))
                        .filter(visible)
                        .filter((el) => {
                            const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                            return !disabled && norm(el.innerText || el.textContent) === label;
                        });
                    const target = controls[controls.length - 1];
                    if (!target) {
                        return false;
                    }
                    target.click();
                    return true;
                }
                """,
                text,
            ):
                return
        except Exception as exc:
            last_error = exc
        try:
            locator = page.get_by_role("button", name=re.compile(r"^\s*%s\s*$" % re.escape(text), re.I))
            count = min(locator.count(), 10)
            for index in range(count - 1, -1, -1):
                button = locator.nth(index)
                try:
                    if button.is_visible(timeout=300) and button.is_enabled(timeout=300):
                        button.click(timeout=1000)
                        return
                except Exception as exc:
                    last_error = exc
                    continue
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(250)
    raise RuntimeError("Could not click visible button %s. Last error: %s" % (text, last_error))


def _click_product_confirmation_add_button(page: object) -> bool:
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            clicked = bool(
                page.evaluate(
                    """
                    () => {
                        const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            const box = el.getBoundingClientRect();
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && box.width > 0
                                && box.height > 0
                                && box.bottom >= 0
                                && box.top <= window.innerHeight;
                        };
                        const labels = Array.from(document.querySelectorAll('body *'))
                            .filter(visible)
                            .filter((el) => norm(el.innerText || el.textContent) === 'Product name');
                        for (const label of labels) {
                            let root = label;
                            for (let depth = 0; root && depth < 8; depth += 1, root = root.parentElement) {
                                const box = root.getBoundingClientRect();
                                const text = root.innerText || root.textContent || '';
                                if (box.width < 300 || box.height < 180 || !text.includes('Product name')) {
                                    continue;
                                }
                                const buttons = Array.from(root.querySelectorAll('button,[role="button"]'))
                                    .filter(visible)
                                    .filter((el) => {
                                        const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                                        const text = norm(el.innerText || el.textContent);
                                        return !disabled && (text === 'Add' || text === 'Thêm');
                                    });
                                const target = buttons[buttons.length - 1];
                                if (target) {
                                    target.scrollIntoView({ block: 'center', inline: 'nearest' });
                                    target.click();
                                    return true;
                                }
                            }
                        }
                        const candidates = Array.from(document.querySelectorAll('button,[role="button"]'))
                            .filter(visible)
                            .map((el) => ({ el, box: el.getBoundingClientRect(), text: norm(el.innerText || el.textContent) }))
                            .filter((item) => item.text === 'Add' || item.text === 'Thêm')
                            .filter((item) => item.box.top > window.innerHeight * 0.45 && item.box.left > window.innerWidth * 0.45);
                        const target = candidates[candidates.length - 1];
                        if (target) {
                            target.el.click();
                            return true;
                        }
                        return false;
                    }
                    """
                )
            )
            if clicked:
                return True
        except Exception:
            pass
        page.wait_for_timeout(250)
    return False


def _set_product_confirmation_name(page: object, value: str) -> None:
    deadline = time.time() + 10
    last_error = None
    while time.time() < deadline:
        try:
            box = page.evaluate(
                """
                () => {
                    const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const labels = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .filter((el) => norm(el.innerText || el.textContent) === 'Product name');
                    for (const label of labels) {
                        let root = label;
                        for (let depth = 0; root && depth < 8; depth += 1, root = root.parentElement) {
                            const rootBox = root.getBoundingClientRect();
                            const text = root.innerText || root.textContent || '';
                            if (rootBox.width < 300 || rootBox.height < 180 || !text.includes('Product name')) {
                                continue;
                            }
                            const fields = Array.from(root.querySelectorAll('input,textarea,[contenteditable="true"]'))
                                .filter(visible);
                            const field = fields[0];
                            if (!field) {
                                continue;
                            }
                            field.scrollIntoView({ block: 'center', inline: 'nearest' });
                            const fieldBox = field.getBoundingClientRect();
                            return {
                                x: fieldBox.left + Math.min(fieldBox.width - 12, Math.max(12, fieldBox.width / 2)),
                                y: fieldBox.top + fieldBox.height / 2
                            };
                        }
                    }
                    return null;
                }
                """
            )
            if box:
                page.mouse.click(box["x"], box["y"])
                page.wait_for_timeout(150)
                focused = bool(
                    page.evaluate(
                        """
                        () => {
                            const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                            const active = document.activeElement;
                            const editable = active && (
                                active.matches('input,textarea,[contenteditable="true"]')
                                || active.closest('[contenteditable="true"]')
                            );
                            if (!editable) {
                                return false;
                            }
                            const labels = Array.from(document.querySelectorAll('body *'))
                                .filter((el) => norm(el.innerText || el.textContent) === 'Product name');
                            for (const label of labels) {
                                let root = label;
                                for (let depth = 0; root && depth < 8; depth += 1, root = root.parentElement) {
                                    const text = root.innerText || root.textContent || '';
                                    if (text.includes('Product name') && root.contains(active)) {
                                        return true;
                                    }
                                }
                            }
                            return false;
                        }
                        """
                    )
                )
                if not focused:
                    page.wait_for_timeout(250)
                    continue
                page.keyboard.press("Control+A")
                page.wait_for_timeout(80)
                page.keyboard.press("Backspace")
                page.wait_for_timeout(120)
                page.keyboard.type(value, delay=20)
            else:
                updated = bool(
                    page.evaluate(
                        """
                        (value) => {
                            const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                            const visible = (el) => {
                                const style = window.getComputedStyle(el);
                                const box = el.getBoundingClientRect();
                                return style.visibility !== 'hidden'
                                    && style.display !== 'none'
                                    && box.width > 0
                                    && box.height > 0
                                    && box.bottom >= 0
                                    && box.top <= window.innerHeight;
                            };
                            const labels = Array.from(document.querySelectorAll('body *'))
                                .filter(visible)
                                .filter((el) => norm(el.innerText || el.textContent) === 'Product name');
                            for (const label of labels) {
                                let root = label;
                                for (let depth = 0; root && depth < 8; depth += 1, root = root.parentElement) {
                                    const box = root.getBoundingClientRect();
                                    const text = root.innerText || root.textContent || '';
                                    if (box.width < 300 || box.height < 180 || !text.includes('Product name')) {
                                        continue;
                                    }
                                    const fields = Array.from(root.querySelectorAll('input,textarea,[contenteditable="true"]'))
                                        .filter(visible);
                                    const field = fields[0];
                                    if (!field) {
                                        continue;
                                    }
                                    field.focus();
                                    if (field.isContentEditable) {
                                        document.execCommand('selectAll', false, null);
                                        document.execCommand('insertText', false, value);
                                    } else {
                                        const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), 'value')?.set;
                                        if (setter) {
                                            setter.call(field, value);
                                        } else {
                                            field.value = value;
                                        }
                                        field.dispatchEvent(new Event('input', { bubbles: true }));
                                        field.dispatchEvent(new Event('change', { bubbles: true }));
                                    }
                                    return true;
                                }
                            }
                            return false;
                        }
                        """,
                        value,
                    )
                )
                if not updated:
                    page.wait_for_timeout(250)
                    continue
            _wait_until(
                page,
                lambda: _product_confirmation_name_is(page, value),
                timeout=3000,
                error="Product name did not update to %s." % value,
            )
            return
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(250)
    raise RuntimeError("Could not set product name to %s. Last error: %s" % (value, last_error))


def _product_confirmation_name_is(page: object, value: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (value) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    return Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]'))
                        .filter(visible)
                        .some((el) => (el.isContentEditable ? el.textContent : el.value) === value);
                }
                """,
                value,
            )
        )
    except Exception:
        return False


def _product_confirmation_ready(page: object, product_id: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0
                            && box.bottom >= 0
                            && box.top <= window.innerHeight;
                    };
                    const labels = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .filter((el) => norm(el.innerText || el.textContent) === 'Product name');
                    for (const label of labels) {
                        let root = label;
                        for (let depth = 0; root && depth < 8; depth += 1, root = root.parentElement) {
                            const box = root.getBoundingClientRect();
                            const text = root.innerText || root.textContent || '';
                            if (box.width < 300 || box.height < 180 || !text.includes('Product name')) {
                                continue;
                            }
                            const field = Array.from(root.querySelectorAll('input,textarea,[contenteditable="true"]'))
                                .filter(visible)[0];
                            if (!field) {
                                continue;
                            }
                            const addButton = Array.from(root.querySelectorAll('button,[role="button"]'))
                                .filter(visible)
                                .find((el) => {
                                    const text = norm(el.innerText || el.textContent);
                                    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                                    return !disabled && (text === 'Add' || text === 'Thêm');
                                });
                            if (addButton) {
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


def _wait_for_product_attached(page: object, product_id: str) -> None:
    _wait_until(
        page,
        lambda: _product_attached(page),
        timeout=15000,
        error="Product confirmation dialog did not close after clicking Add.",
    )
    page.wait_for_timeout(500)


def _product_attached(page: object) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    return !Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .some((el) => ['Product name', 'Add product links', 'Link type'].includes(norm(el.innerText || el.textContent)));
                }
                """
            )
        )
    except Exception:
        return not _page_has_visible_text(page, "Product name")


def _page_has_visible_text(page: object, text: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (needle) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && box.width > 0
                            && box.height > 0;
                    };
                    return Array.from(document.querySelectorAll('body *'))
                        .some((el) => visible(el) && (el.innerText || el.textContent || '').includes(needle));
                }
                """,
                text,
            )
        )
    except Exception:
        try:
            return page.get_by_text(text, exact=False).first.is_visible(timeout=300)
        except Exception:
            return False


def _wait_until(page: object, predicate, timeout: int, error: str) -> None:
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if predicate():
            return
        page.wait_for_timeout(250)
    raise RuntimeError(error)
