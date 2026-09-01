"""Local settings used by the Gemini-powered Fashion workspace."""

from __future__ import annotations

from PySide6.QtCore import QSettings


_ORGANIZATION = "AutoTikTokEditor"
_APPLICATION = "TikTokProfileManager"
_GEMINI_API_KEY_SETTING = "gemini/api_key"
_GEMINI_MODEL_SETTING = "gemini/model"


def get_gemini_api_key() -> str:
    """Return the locally stored Gemini API key, or an empty string."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    return str(settings.value(_GEMINI_API_KEY_SETTING, "") or "").strip()


def save_gemini_api_key(api_key: str) -> None:
    """Persist the Gemini API key in this user's application settings."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue(_GEMINI_API_KEY_SETTING, str(api_key or "").strip())
    settings.sync()


def get_gemini_model(default: str) -> str:
    """Return the selected Gemini model, falling back to the supplied default."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    return str(settings.value(_GEMINI_MODEL_SETTING, default) or default).strip() or default


def save_gemini_model(model: str) -> None:
    """Persist the model selected in the Fashion workspace."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue(_GEMINI_MODEL_SETTING, str(model or "").strip())
    settings.sync()
