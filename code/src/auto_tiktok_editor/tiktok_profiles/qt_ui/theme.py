"""Theme, constants, and formatting utilities for Qt UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

from auto_tiktok_editor.tiktok_profiles.profile_manager import (
    default_hashtag_for_account_name,
    normalize_hashtags,
    slugify,
)

from qfluentwidgets import FluentIconBase, Theme, isDarkTheme

VIETNAM_TZ = timezone(timedelta(hours=7))


class ModernPhoneIcon(FluentIconBase):
    """Modern smartphone Fluent icon for mobile tab & status."""

    def path(self, theme=Theme.AUTO) -> str:
        is_dark = isDarkTheme() if theme == Theme.AUTO else (theme == Theme.DARK)
        color = "white" if is_dark else "black"
        return str(Path(__file__).parent / "assets" / "icons" / f"phone_{color}.svg")

VIDEO_CUT_MODE_LABELS = {
    "fixed": "Cắt cố định",
    "scene": "Cắt theo đổi cảnh",
    "original": "Giữ nguyên video gốc",
}
VIDEO_CUT_MODE_VALUES = {label: value for value, label in VIDEO_CUT_MODE_LABELS.items()}

VIDEO_ROW_CUT_MODE_LABELS = {
    "fixed": "Cắt cố định",
    "scene": "Cắt theo đổi cảnh",
    "original": "Giữ nguyên video gốc",
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

# --- THEME STORAGE & RUNTIME MODE ---
_SETTINGS = QSettings("AutoTikTokEditor", "TikTokProfileManager")
_RUNTIME_THEME_MODE: str = str(_SETTINGS.value("theme_mode", "light")).strip().lower()


def get_current_theme_mode() -> str:
    """Return active theme mode: 'light' or 'dark'."""
    global _RUNTIME_THEME_MODE
    return _RUNTIME_THEME_MODE if _RUNTIME_THEME_MODE in ("light", "dark") else "light"


def set_current_theme_mode(mode: str) -> None:
    """Set active theme mode: 'light' or 'dark' and persist in QSettings."""
    global _RUNTIME_THEME_MODE
    clean_mode = "dark" if str(mode).strip().lower() == "dark" else "light"
    _RUNTIME_THEME_MODE = clean_mode
    _SETTINGS.setValue("theme_mode", clean_mode)


# ==========================================
# 1. LIGHT THEME PALETTE
# ==========================================
LIGHT_APP_BG = "#F6F7FB"
LIGHT_SIDEBAR_BG = "#F1F3F9"
LIGHT_CARD = "#FFFFFF"
LIGHT_CARD_HOVER = "#FAFAFF"
LIGHT_BORDER = "#E4E7EF"
LIGHT_DIVIDER = "#ECEEF4"

LIGHT_ACCENT = "#6D5DFB"
LIGHT_ACCENT_HOVER = "#5B4CF0"
LIGHT_ACCENT_ACTIVE = "#4C3ED9"
LIGHT_ACCENT_SOFT = "#EEECFF"
LIGHT_ACCENT_BORDER = "#D9D5FF"

LIGHT_TEXT_MAIN = "#181B2A"
LIGHT_TEXT_SECONDARY = "#5F6475"
LIGHT_TEXT_MUTED = "#9095A5"
LIGHT_TEXT_DISABLED = "#B7BBC7"

LIGHT_SIDEBAR_SELECTED_BG = "#EDEBFF"
LIGHT_SIDEBAR_SELECTED_TEXT = "#5B4CF0"

LIGHT_INPUT_BG = "#FFFFFF"
LIGHT_INPUT_BORDER = "#DDE1EA"
LIGHT_INPUT_FOCUS = "#6D5DFB"

LIGHT_SUCCESS = "#16A36A"
LIGHT_WARNING = "#E89B20"
LIGHT_ERROR = "#E5484D"
LIGHT_INFO = "#3B82F6"

# ==========================================
# 2. DARK THEME PALETTE
# ==========================================
DARK_APP_BG = "#11131A"
DARK_SIDEBAR_BG = "#151821"
DARK_CARD = "#1A1D27"
DARK_CARD_HOVER = "#202431"
DARK_BORDER = "#2A2F3D"
DARK_DIVIDER = "#232733"

DARK_ACCENT = "#8B7CFF"
DARK_ACCENT_HOVER = "#9D91FF"
DARK_ACCENT_ACTIVE = "#7666F5"
DARK_ACCENT_SOFT = "#27233F"
DARK_ACCENT_BORDER = "#423A72"

DARK_TEXT_MAIN = "#F3F4F8"
DARK_TEXT_SECONDARY = "#B5B9C7"
DARK_TEXT_MUTED = "#7F8596"
DARK_TEXT_DISABLED = "#555B69"

DARK_SIDEBAR_SELECTED_BG = "#27233F"
DARK_SIDEBAR_SELECTED_TEXT = "#A69BFF"

DARK_INPUT_BG = "#171A23"
DARK_INPUT_BORDER = "#303543"
DARK_INPUT_FOCUS = "#8B7CFF"

DARK_SUCCESS = "#32C985"
DARK_WARNING = "#F2B84B"
DARK_ERROR = "#F06A6A"
DARK_INFO = "#60A5FA"

# Status Definitions
VIDEO_STATUS_LABELS = {
    "ready": "● Sẵn sàng",
    "prepared": "● Đã chuẩn bị",
    "published": "● Đã đăng",
    "rendering": "● Đang tạo",
    "queued": "● Trong hàng đợi",
    "scheduled": "● Đã đặt lịch",
    "draft": "● Bản nháp",
    "pending": "● Chờ xử lý",
    "error": "● Lỗi",
    "failed": "● Lỗi",
    "paused": "● Tạm dừng",
}

VIDEO_STATUS_COLORS_LIGHT = {
    "ready": "#16A36A",       # Success
    "prepared": "#16A36A",    # Success
    "published": "#16A36A",   # Success
    "rendering": "#3B82F6",   # Info
    "queued": "#6D5DFB",      # Primary Accent
    "scheduled": "#6D5DFB",   # Primary Accent
    "draft": "#E89B20",       # Warning
    "pending": "#E89B20",     # Warning
    "error": "#E5484D",       # Error
    "failed": "#E5484D",      # Error
    "paused": "#9095A5",      # Muted
}

VIDEO_STATUS_COLORS_DARK = {
    "ready": "#32C985",       # Success
    "prepared": "#32C985",    # Success
    "published": "#32C985",   # Success
    "rendering": "#60A5FA",   # Info
    "queued": "#8B7CFF",      # Primary Accent
    "scheduled": "#8B7CFF",   # Primary Accent
    "draft": "#F2B84B",       # Warning
    "pending": "#F2B84B",     # Warning
    "error": "#F06A6A",       # Error
    "failed": "#F06A6A",      # Error
    "paused": "#7F8596",      # Muted
}


def format_video_status(status: str, mode: str | None = None) -> tuple[str, str]:
    """Returns (display_text_vietnamese_with_dot, hex_color)."""
    theme_mode = (mode or get_current_theme_mode()).lower()
    raw = str(status or "pending").strip().lower()
    colors = VIDEO_STATUS_COLORS_DARK if theme_mode == "dark" else VIDEO_STATUS_COLORS_LIGHT
    fallback_color = "#7F8596" if theme_mode == "dark" else "#9095A5"
    for key, label in VIDEO_STATUS_LABELS.items():
        if key in raw:
            return label, colors.get(key, fallback_color)
    return f"● {raw.capitalize()}", fallback_color


ACCOUNT_STATUS_LABELS = {
    "ready": "● Sẵn sàng",
    "live": "● Đang Live",
    "login_required": "● Cần đăng nhập",
    "error": "● Lỗi",
    "unknown": "● Chưa rõ",
}

ACCOUNT_STATUS_COLORS_LIGHT = {
    "ready": "#16A36A",       # Success
    "live": "#3B82F6",        # Info
    "login_required": "#E5484D", # Error
    "error": "#E5484D",       # Error
    "unknown": "#E89B20",     # Warning
}

ACCOUNT_STATUS_COLORS_DARK = {
    "ready": "#32C985",       # Success
    "live": "#60A5FA",        # Info
    "login_required": "#F06A6A", # Error
    "error": "#F06A6A",       # Error
    "unknown": "#F2B84B",     # Warning
}


def format_account_status(status: str, mode: str | None = None) -> tuple[str, str]:
    """Returns (display_text_vietnamese_with_dot, hex_color)."""
    theme_mode = (mode or get_current_theme_mode()).lower()
    raw = str(status or "unknown").strip().lower()
    colors = ACCOUNT_STATUS_COLORS_DARK if theme_mode == "dark" else ACCOUNT_STATUS_COLORS_LIGHT
    fallback_color = "#7F8596" if theme_mode == "dark" else "#9095A5"
    for key, label in ACCOUNT_STATUS_LABELS.items():
        if key in raw:
            return label, colors.get(key, fallback_color)
    return f"● {raw.capitalize()}", fallback_color


# ==========================================
# 3. LIGHT MODE STYLESHEET
# ==========================================
MODERN_LIGHT_STYLESHEET = """
FluentWindow {
    background-color: #F6F7FB;
}

#dashboardInterface, #accountsInterface, #sourcesInterface, #videosInterface, #phoneInterface, #telegramInterface, #logsInterface, #settingsInterface {
    background-color: #F6F7FB;
}

QScrollArea, SmoothScrollArea, SmoothScrollArea > QWidget, SmoothScrollArea > QWidget > QWidget {
    background-color: transparent;
    border: none;
}

/* Sidebar / Navigation */
NavigationPanel, NavigationInterface, #navigationInterface, QWidget#navigationInterface {
    background-color: #F1F3F9;
    border-right: 1px solid #E4E7EF;
}

NavigationButton {
    background-color: transparent;
    color: #181B2A;
    border-radius: 6px;
}

NavigationButton:hover {
    background-color: #E9ECF4;
    color: #181B2A;
}

NavigationButton[isSelected=true] {
    background-color: #EDEBFF;
    color: #5B4CF0;
    font-weight: 600;
}

/* Card / Panel */
CardWidget {
    background-color: #FFFFFF;
    border: 1px solid #E4E7EF;
    border-radius: 10px;
}

CardWidget:hover {
    background-color: #FAFAFF;
    border-color: #D9D5FF;
}

/* Primary Button */
PrimaryPushButton {
    background-color: #6D5DFB;
    color: #FFFFFF;
    font-weight: 600;
    border-radius: 8px;
    border: 1px solid #6D5DFB;
    padding: 7px 16px;
    font-size: 13px;
}

PrimaryPushButton:hover {
    background-color: #5B4CF0;
    border-color: #5B4CF0;
}

PrimaryPushButton:pressed {
    background-color: #4C3ED9;
    border-color: #4C3ED9;
}

/* Standard Buttons */
PushButton, ToolButton {
    background-color: #FFFFFF;
    border: 1px solid #DDE1EA;
    color: #181B2A;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
}

PushButton:hover, ToolButton:hover {
    background-color: #F1F3F9;
    border-color: #CBD2E1;
    color: #181B2A;
}

PushButton:pressed, ToolButton:pressed {
    background-color: #E4E7EF;
    border-color: #DDE1EA;
    color: #181B2A;
}

/* Table Widget */
TableWidget, QTableView {
    background-color: #FFFFFF;
    alternate-background-color: #F8FAFD;
    border: 1px solid #E4E7EF;
    border-radius: 10px;
    color: #181B2A;
    gridline-color: transparent;
    selection-background-color: #EEECFF;
    selection-color: #181B2A;
    padding: 4px;
}

QHeaderView::section {
    background-color: #F1F3F9;
    color: #5F6475;
    font-weight: 600;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid #E4E7EF;
    padding: 8px 10px;
}

/* Form Inputs */
LineEdit, ComboBox, SpinBox, DoubleSpinBox, PlainTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #DDE1EA;
    border-radius: 8px;
    color: #181B2A;
    padding: 6px 10px;
    font-size: 13px;
}

LineEdit:focus, ComboBox:focus, SpinBox:focus, DoubleSpinBox:focus, PlainTextEdit:focus {
    border: 1px solid #6D5DFB;
    background-color: #FFFFFF;
}

/* ScrollBar */
QScrollBar:vertical {
    background: #F6F7FB;
    width: 8px;
    margin: 0;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #DDE1EA;
    min-height: 24px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #6D5DFB;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

# ==========================================
# 4. DARK MODE STYLESHEET
# ==========================================
MODERN_DARK_STYLESHEET = """
FluentWindow {
    background-color: #11131A;
}

#dashboardInterface, #accountsInterface, #sourcesInterface, #videosInterface, #phoneInterface, #telegramInterface, #logsInterface, #settingsInterface {
    background-color: #11131A;
}

QScrollArea, SmoothScrollArea, SmoothScrollArea > QWidget, SmoothScrollArea > QWidget > QWidget {
    background-color: transparent;
    border: none;
}

/* Sidebar / Navigation */
NavigationPanel, NavigationInterface, #navigationInterface, QWidget#navigationInterface {
    background-color: #151821;
    border-right: 1px solid #2A2F3D;
}

NavigationButton {
    background-color: transparent;
    color: #F3F4F8;
    border-radius: 6px;
}

NavigationButton:hover {
    background-color: #202431;
    color: #F3F4F8;
}

NavigationButton[isSelected=true] {
    background-color: #27233F;
    color: #A69BFF;
    font-weight: 600;
}

/* Card / Panel */
CardWidget {
    background-color: #1A1D27;
    border: 1px solid #2A2F3D;
    border-radius: 10px;
}

CardWidget:hover {
    background-color: #202431;
    border-color: #423A72;
}

/* Primary Button */
PrimaryPushButton {
    background-color: #8B7CFF;
    color: #FFFFFF;
    font-weight: 600;
    border-radius: 8px;
    border: 1px solid #8B7CFF;
    padding: 7px 16px;
    font-size: 13px;
}

PrimaryPushButton:hover {
    background-color: #9D91FF;
    border-color: #9D91FF;
}

PrimaryPushButton:pressed {
    background-color: #7666F5;
    border-color: #7666F5;
}

/* Standard Buttons */
PushButton, ToolButton {
    background-color: #1A1D27;
    border: 1px solid #303543;
    color: #F3F4F8;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
}

PushButton:hover, ToolButton:hover {
    background-color: #202431;
    border-color: #423A72;
    color: #FFFFFF;
}

PushButton:pressed, ToolButton:pressed {
    background-color: #151821;
    border-color: #2A2F3D;
    color: #B5B9C7;
}

/* Table Widget */
TableWidget, QTableView {
    background-color: #171A23;
    alternate-background-color: #11131A;
    border: 1px solid #2A2F3D;
    border-radius: 10px;
    color: #F3F4F8;
    gridline-color: transparent;
    selection-background-color: #27233F;
    selection-color: #F3F4F8;
    padding: 4px;
}

QHeaderView::section {
    background-color: #151821;
    color: #B5B9C7;
    font-weight: 600;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid #2A2F3D;
    padding: 8px 10px;
}

/* Form Inputs */
LineEdit, ComboBox, SpinBox, DoubleSpinBox, PlainTextEdit {
    background-color: #171A23;
    border: 1px solid #303543;
    border-radius: 8px;
    color: #F3F4F8;
    padding: 6px 10px;
    font-size: 13px;
}

LineEdit:focus, ComboBox:focus, SpinBox:focus, DoubleSpinBox:focus, PlainTextEdit:focus {
    border: 1px solid #8B7CFF;
    background-color: #1A1D27;
}

/* ScrollBar */
QScrollBar:vertical {
    background: #11131A;
    width: 8px;
    margin: 0;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #303543;
    min-height: 24px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #8B7CFF;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


TIKTOK_ANDROID_PACKAGES = (
    "com.ss.android.ugc.trill",
    "com.ss.android.ugc.aweme",
    "com.zhiliaoapp.musically",
    "com.zhiliaoapp.musically.go",
)


def format_vietnam_datetime(value: str, assume_utc: bool = True) -> str:
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


def default_schedule_time_text() -> str:
    return (datetime.now() + timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat(sep=" ", timespec="minutes")


def parse_schedule_time(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def extract_product_url_from_note(note: str) -> str:
    for line in (note or "").splitlines():
        text = line.strip()
        if "product link:" not in text.lower():
            continue
        value = text.split(":", 1)[1].strip()
        url = value.split()[0].rstrip(".,)]}") if value else ""
        if url.startswith(("http://", "https://")):
            return url
    return ""


def compose_video_caption_with_hashtags(caption: str, hashtags: str) -> str:
    parts = []
    clean_caption = str(caption or "").strip()
    clean_hashtags = normalize_hashtags(hashtags)
    if clean_caption:
        parts.append(clean_caption)
    if clean_hashtags:
        parts.append(clean_hashtags)
    return "\n".join(parts).strip()


def telegram_product_messages_for_video(video) -> tuple[str, str]:
    caption_message = compose_video_caption_with_hashtags(video.caption, video.hashtags)
    product_id = str(video.product_id or "").strip()
    video_id = getattr(video, "id", "")
    if not caption_message:
        raise ValueError(f"Video {video_id} chưa có caption/hashtags để gửi Telegram.")
    if product_id and not product_id.isdigit():
        raise ValueError(f"Video {video_id} có Product ID không hợp lệ: {product_id}")
    return caption_message, product_id


def account_profile_slugs(account) -> set[str]:
    values = {
        slugify(getattr(account, "bot_name", "") or ""),
        slugify(getattr(account, "name", "") or ""),
        slugify(Path(getattr(account, "profile_path", "") or "").name),
    }
    return {value for value in values if value}


def telegram_bot_name_matches_account(bot_name: str, account_slugs: set[str]) -> bool:
    bot_slug = slugify(bot_name)
    if bot_slug in account_slugs:
        return True
    return bot_slug.endswith("_bot") and bot_slug[:-4] in account_slugs


def telegram_bot_chat_id(item: dict) -> int | None:
    value = item.get("chat_id") or item.get("delivery_chat_id")
    if value is None and isinstance(item.get("chat_ids"), list) and item.get("chat_ids"):
        value = item.get("chat_ids")[0]
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("chat_id must be a valid integer.")


def telegram_bot_config_for_account(payload: dict, account) -> tuple[str, int]:
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
            chat_id = telegram_bot_chat_id(item)
            if not token:
                raise ValueError(f"Bot {bot_name} is missing bot_token.")
            if chat_id is None:
                raise ValueError(f"Bot {bot_name} is missing chat_id.")
            return token, chat_id
        raise ValueError(f"No Telegram bot config matches bot_name {getattr(account, 'bot_name', '')}.")
    candidates = account_profile_slugs(account)
    for item in bots:
        if not isinstance(item, dict):
            continue
        bot_name = str(item.get("name") or "").strip()
        if not telegram_bot_name_matches_account(bot_name, candidates):
            continue
        token = str(item.get("bot_token") or item.get("token") or "").strip()
        chat_id = telegram_bot_chat_id(item)
        if not token:
            raise ValueError(f"Bot {bot_name} is missing bot_token.")
        if chat_id is None:
            raise ValueError(f"Bot {bot_name} is missing chat_id.")
        return token, chat_id
    raise ValueError(f"No Telegram bot config matches profile {getattr(account, 'name', '')}.")
