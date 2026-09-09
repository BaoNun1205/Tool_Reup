"""Canonical Fashion product categories shared by Gemini, storage, and UI."""

from __future__ import annotations

import re
import unicodedata


FASHION_PRODUCT_CATEGORIES = (
    "Áo Thun",
    "Áo Sơ Mi",
    "Áo Khoác",
    "Quần Jean",
    "Quần Short",
)
UNCATEGORIZED_FASHION_PRODUCT = "Chưa phân loại"


def _category_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


_CATEGORY_ALIASES = {
    "aothun": "Áo Thun",
    "tshirt": "Áo Thun",
    "tee": "Áo Thun",
    "aosomi": "Áo Sơ Mi",
    "shirt": "Áo Sơ Mi",
    "aokhoac": "Áo Khoác",
    "jacket": "Áo Khoác",
    "coat": "Áo Khoác",
    "quanjean": "Quần Jean",
    "quanjeans": "Quần Jean",
    "jean": "Quần Jean",
    "jeans": "Quần Jean",
    "quanshort": "Quần Short",
    "quanshorts": "Quần Short",
    "short": "Quần Short",
    "shorts": "Quần Short",
}


def normalize_fashion_product_category(value: object) -> str:
    """Return one canonical category label, or an empty string when unsupported."""
    return _CATEGORY_ALIASES.get(_category_key(value), "")
