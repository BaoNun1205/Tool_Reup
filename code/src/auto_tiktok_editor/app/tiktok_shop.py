"""Resolve TikTok Shop share links without relying on a paid third-party API."""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 20
MAX_IMAGE_BYTES = 14 * 1024 * 1024
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTRIBUTE_RE = re.compile(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", re.IGNORECASE | re.DOTALL)
_PRODUCT_ID_RE = re.compile(r"(?<!\d)(\d{12,22})(?!\d)")


class TikTokShopResolveError(RuntimeError):
    """A user-facing error while resolving a TikTok Shop link."""


@dataclass(frozen=True)
class TikTokShopProduct:
    source_url: str
    resolved_url: str
    product_id: str
    title: str
    image_url: str


def resolve_tiktok_shop_product(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> TikTokShopProduct:
    """Follow a Shop/share URL and extract title, image and product ID from ``og_info``.

    TikTok Shop share links currently put the Open Graph payload in the final
    redirect URL.  A meta-tag fallback keeps the resolver useful for links
    where TikTok omits that parameter.
    """

    source_url = str(url or "").strip()
    parsed_source = urlparse(source_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        raise TikTokShopResolveError("Link sản phẩm phải bắt đầu bằng http:// hoặc https://.")

    request = Request(source_url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
    try:
        with urlopen(request, timeout=max(1, int(timeout))) as response:
            resolved_url = str(response.geturl() or source_url)
            content_type = str(response.headers.get("Content-Type") or "")
            response_body = response.read(512 * 1024) if "html" in content_type.lower() else b""
    except Exception as exc:
        raise TikTokShopResolveError("Không thể mở link TikTok Shop. Hãy thử lại sau.") from exc

    og_info = _og_info_from_url(resolved_url)
    if not og_info:
        og_info = _og_info_from_page(response_body)

    title = str(og_info.get("title") or "").strip()
    image_url = str(og_info.get("image") or og_info.get("image_url") or "").strip()
    product_id = _extract_product_id(resolved_url, og_info)
    if not title:
        raise TikTokShopResolveError("Không tìm thấy tên sản phẩm trong link TikTok Shop này.")
    if not image_url or urlparse(image_url).scheme not in {"http", "https"}:
        raise TikTokShopResolveError("Không tìm thấy ảnh sản phẩm trong link TikTok Shop này.")
    if not product_id:
        raise TikTokShopResolveError("Không tìm thấy Product ID trong link TikTok Shop này.")
    return TikTokShopProduct(
        source_url=source_url,
        resolved_url=resolved_url,
        product_id=product_id,
        title=title,
        image_url=image_url,
    )


def download_tiktok_shop_product_image(product: TikTokShopProduct, destination_dir: Path) -> Path:
    """Download the product thumbnail to the Fashion workspace."""

    destination = Path(destination_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    request = Request(product.image_url, headers={"User-Agent": _USER_AGENT, "Referer": product.resolved_url})
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            image_data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = str(response.headers.get("Content-Type") or "")
    except Exception as exc:
        raise TikTokShopResolveError("Không thể tải ảnh sản phẩm từ TikTok Shop.") from exc
    if not image_data or len(image_data) > MAX_IMAGE_BYTES:
        raise TikTokShopResolveError("Ảnh sản phẩm không hợp lệ hoặc lớn hơn 14 MB.")

    suffix = _image_suffix(product.image_url, content_type)
    safe_product_id = re.sub(r"[^0-9A-Za-z_-]+", "_", product.product_id).strip("_") or "product"
    target = destination / ("%s%s" % (safe_product_id, suffix))
    index = 2
    while target.exists():
        target = destination / ("%s_%d%s" % (safe_product_id, index, suffix))
        index += 1
    target.write_bytes(image_data)
    return target


def _og_info_from_url(value: str) -> dict[str, Any]:
    raw_value = (parse_qs(urlparse(value).query).get("og_info") or [""])[0]
    if not raw_value:
        return {}
    for candidate in (raw_value, unquote(raw_value)):
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _og_info_from_page(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return {}
    values: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(text):
        attrs = {name.lower(): html.unescape(value).strip() for name, _quote, value in _META_ATTRIBUTE_RE.findall(tag)}
        property_name = attrs.get("property") or attrs.get("name") or ""
        content = attrs.get("content") or ""
        if property_name.lower() == "og:title" and content:
            values["title"] = content
        elif property_name.lower() == "og:image" and content:
            values["image"] = content
    return values


def _extract_product_id(resolved_url: str, og_info: dict[str, Any]) -> str:
    for key in ("product_id", "productId", "id"):
        candidate = str(og_info.get(key) or "").strip()
        if candidate.isdigit() and 12 <= len(candidate) <= 22:
            return candidate
    query = parse_qs(urlparse(resolved_url).query)
    for key in ("product_id", "productId", "id"):
        candidate = str((query.get(key) or [""])[0]).strip()
        if candidate.isdigit() and 12 <= len(candidate) <= 22:
            return candidate
    match = _PRODUCT_ID_RE.search(unquote(resolved_url))
    return match.group(1) if match else ""


def _image_suffix(image_url: str, content_type: str) -> str:
    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    content_type = content_type.split(";", 1)[0].lower().strip()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".jpg")
