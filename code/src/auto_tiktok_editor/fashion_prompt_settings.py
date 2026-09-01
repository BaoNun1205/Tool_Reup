"""Persistence helpers for user-edited fashion prompt presets."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QSettings

from auto_tiktok_editor.fashion_prompts import (
    DEFAULT_CHANGE_OUTFIT_PROMPT,
    FASHION_PROMPT_PRESETS,
    FashionPromptPreset,
    is_change_outfit_prompt_name,
)


_ORGANIZATION = "AutoTikTokEditor"
_APPLICATION = "TikTokProfileManager"
_GARMENT_TYPES_KEY = "fashion_prompts/custom_garment_types"


def load_garment_presets() -> tuple[FashionPromptPreset, ...]:
    """Load the built-in clothing types followed by user-created clothing types."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    raw_value = settings.value(_GARMENT_TYPES_KEY, "")
    try:
        custom_types = json.loads(str(raw_value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        custom_types = []

    presets = list(FASHION_PROMPT_PRESETS)
    known_keys = {preset.key for preset in presets}
    if not isinstance(custom_types, list):
        return tuple(presets)
    for item in custom_types:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        if not key or not label or key in known_keys:
            continue
        presets.append(FashionPromptPreset(key=key, label=label, first_scene="", second_scene=""))
        known_keys.add(key)
    return tuple(presets)


def add_garment_preset(label: str) -> FashionPromptPreset:
    """Create and persist a custom clothing type with no starter prompts."""
    clean_label = str(label or "").strip()
    if not clean_label:
        raise ValueError("Tên loại không được để trống.")

    presets = load_garment_presets()
    if any(preset.label.casefold() == clean_label.casefold() for preset in presets):
        raise ValueError("Loại này đã tồn tại.")

    slug = re.sub(r"[^a-z0-9]+", "_", clean_label.lower()).strip("_") or "loai_ao"
    known_keys = {preset.key for preset in presets}
    key = f"custom_{slug}"
    suffix = 2
    while key in known_keys:
        key = f"custom_{slug}_{suffix}"
        suffix += 1

    settings = QSettings(_ORGANIZATION, _APPLICATION)
    raw_value = settings.value(_GARMENT_TYPES_KEY, "")
    try:
        custom_types = json.loads(str(raw_value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        custom_types = []
    if not isinstance(custom_types, list):
        custom_types = []
    custom_types.append({"key": key, "label": clean_label})
    settings.setValue(_GARMENT_TYPES_KEY, json.dumps(custom_types, ensure_ascii=False))
    settings.sync()
    return FashionPromptPreset(key=key, label=clean_label, first_scene="", second_scene="")


def load_fashion_prompt_texts(preset: FashionPromptPreset) -> tuple[str, str]:
    """Load edited scene prompts, using built-in text when no edit was saved."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    base = f"fashion_prompts/{preset.key}"
    first = settings.value(f"{base}/first_scene", None)
    second = settings.value(f"{base}/second_scene", None)
    return (
        preset.first_scene if first is None else str(first),
        preset.second_scene if second is None else str(second),
    )


def load_extra_fashion_prompts(preset: FashionPromptPreset) -> list[tuple[str, str]]:
    """Load user-created named prompts for one clothing type."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    raw_value = settings.value(f"fashion_prompts/{preset.key}/extra_prompts", "")
    try:
        saved_prompts = json.loads(str(raw_value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(saved_prompts, list):
        return []
    result: list[tuple[str, str]] = []
    for item in saved_prompts:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        content = str(item.get("content") or "").strip()
        if name and content:
            result.append((name, content))
    return result


def _save_extra_fashion_prompts(preset: FashionPromptPreset, prompts: list[tuple[str, str]]) -> None:
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue(
        f"fashion_prompts/{preset.key}/extra_prompts",
        json.dumps(
            [{"name": name, "content": content} for name, content in prompts],
            ensure_ascii=False,
        ),
    )
    settings.sync()


def load_change_outfit_prompt() -> tuple[str, str]:
    """Return the one shared outfit-change prompt displayed above clothing prompts."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    name = settings.value("fashion_prompts/shared/change_outfit/name", None)
    content = settings.value("fashion_prompts/shared/change_outfit/content", None)
    if name is None or str(name).strip() == "Thay đồ":
        name = DEFAULT_CHANGE_OUTFIT_PROMPT[0]
    return (
        str(name),
        DEFAULT_CHANGE_OUTFIT_PROMPT[1] if content is None else str(content),
    )


def save_change_outfit_prompt(name: str, content: str) -> None:
    """Persist the one shared outfit-change prompt."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue("fashion_prompts/shared/change_outfit/name", str(name))
    settings.setValue("fashion_prompts/shared/change_outfit/content", str(content))
    settings.sync()


def migrate_change_outfit_prompt(preset: FashionPromptPreset) -> list[tuple[str, str]]:
    """Move a legacy 'Thay đồ' entry out of a clothing list into the shared prompt."""
    prompts = load_extra_fashion_prompts(preset)
    shared_matches = [(name, content) for name, content in prompts if is_change_outfit_prompt_name(name)]
    if not shared_matches:
        return prompts

    settings = QSettings(_ORGANIZATION, _APPLICATION)
    has_shared_prompt = settings.value("fashion_prompts/shared/change_outfit/content", None) is not None
    if not has_shared_prompt:
        save_change_outfit_prompt(*shared_matches[0])

    remaining_prompts = [(name, content) for name, content in prompts if not is_change_outfit_prompt_name(name)]
    _save_extra_fashion_prompts(preset, remaining_prompts)
    return remaining_prompts


def load_garment_prompts(preset: FashionPromptPreset) -> list[tuple[str, str]]:
    """Load one unified, editable prompt list for the selected clothing type."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    base = f"fashion_prompts/{preset.key}"
    raw_value = settings.value(f"{base}/prompts", None)
    if raw_value is not None:
        try:
            saved_prompts = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            saved_prompts = []
        if isinstance(saved_prompts, list):
            prompts = []
            for item in saved_prompts:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                content = str(item.get("content") or "").strip()
                if name and content:
                    prompts.append((name, content))
            if prompts or not saved_prompts:
                return prompts

    first_scene, second_scene = load_fashion_prompt_texts(preset)
    if not first_scene and not second_scene:
        return load_extra_fashion_prompts(preset)
    return [
        ("Cảnh đầu", first_scene),
        ("Cảnh sau", second_scene),
        *load_extra_fashion_prompts(preset),
    ]


def save_garment_prompts(preset: FashionPromptPreset, prompts: list[tuple[str, str]]) -> None:
    """Persist every prompt for a clothing type in display order."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.setValue(
        f"fashion_prompts/{preset.key}/prompts",
        json.dumps(
            [{"name": name, "content": content} for name, content in prompts],
            ensure_ascii=False,
        ),
    )
    settings.sync()


def reset_garment_prompts(preset: FashionPromptPreset) -> None:
    """Clear all prompt overrides for a clothing type, including legacy storage."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    settings.remove(f"fashion_prompts/{preset.key}")
    settings.sync()


def save_fashion_prompt_texts(
    preset: FashionPromptPreset,
    first_scene: str,
    second_scene: str,
    extra_prompts: list[tuple[str, str]] | None = None,
) -> None:
    """Persist the user's scene edits for one clothing type."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    base = f"fashion_prompts/{preset.key}"
    settings.setValue(f"{base}/first_scene", str(first_scene))
    settings.setValue(f"{base}/second_scene", str(second_scene))
    settings.setValue(
        f"{base}/extra_prompts",
        json.dumps(
            [{"name": name, "content": content} for name, content in (extra_prompts or [])],
            ensure_ascii=False,
        ),
    )
    settings.sync()


def reset_fashion_prompt_texts(preset: FashionPromptPreset) -> None:
    """Remove saved overrides and restore the built-in prompts on the next load."""
    settings = QSettings(_ORGANIZATION, _APPLICATION)
    base = f"fashion_prompts/{preset.key}"
    settings.remove(f"{base}/first_scene")
    settings.remove(f"{base}/second_scene")
    settings.remove(f"{base}/extra_prompts")
    settings.sync()
