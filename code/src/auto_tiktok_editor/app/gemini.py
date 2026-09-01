"""Small, dependency-free client for Gemini image description requests."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import mimetypes
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
GEMINI_MODELS = (
    ("gemini-3.7-flash", "Gemini 3.7 Flash · Mới nhất"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash · Cân bằng"),
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite · Nhanh, tiết kiệm"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite · Nhanh"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash · Tương thích"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite · Tiết kiệm"),
)
MAX_INLINE_IMAGE_BYTES = 14 * 1024 * 1024
_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

FASHION_PROMPT_INSTRUCTION = """Analyze this fashion image and write one detailed English prompt for an AI video generator.
Describe the subject, clothing and accessories, materials, colors, silhouette, pose, expression, setting,
lighting, camera framing, camera movement, mood, and visual style. Preserve only details visible in the image.
Return only the final prompt, with no title, explanation, markdown, or quotation marks."""

FASHION_PRODUCT_COPY_INSTRUCTION = """You are writing Vietnamese TikTok Shop copy for a Fashion product.
The product name is shown separately in the app and must not appear in the caption.

Using the supplied product image and that exact product name, return ONLY valid JSON in this exact shape:
{{"caption":"...","hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"]}}

Rules:
- Write exactly one short Vietnamese sentence, no more than 12 words. It should sound like a real person casually sharing a product they genuinely like, not an advertisement or an AI-generated review.
- The caption is shown beside the product name in the app. Do NOT mention, repeat, shorten, paraphrase, or hint at the product name or brand in the caption. Focus only on a concise impression from the visible look, such as how easy it is to style or its overall vibe.
- Use plain, conversational Vietnamese with varied wording and a natural rhythm. A small, situational joke is welcome only when it feels effortless.
- Keep the praise modest and believable. Avoid sales clichés and AI-sounding phrases such as "siêu phẩm", "cực phẩm", "cực chất", "không thể bỏ lỡ", "nâng tầm phong cách", "must-have", or exaggerated claims.
- Never invent product specifications, personal experience, discounts, or availability.
- Return exactly 5 distinct hashtags that describe only the specific product: its garment/product type, visible style, color, material, pattern, or brand when clearly shown.
- Never use generic discovery, platform, or trend hashtags, including #xuhuong, #fyp, #viral, #trending, #tiktok, #tiktokshop, #outfit, #fashion, or #thoitrang. Do not add any hashtag that is unrelated to the product itself.
- Do not add markdown, explanations, keys other than caption and hashtags, or text outside the JSON object."""


class GeminiRequestError(RuntimeError):
    """A clear, display-safe error returned by Gemini or the local request setup."""


@dataclass(frozen=True)
class FashionProductCopy:
    caption: str
    hashtags: tuple[str, ...]

    @property
    def description(self) -> str:
        return "%s\n%s" % (self.caption, " ".join(self.hashtags))


def chat_with_gemini(
    message: str,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    image_path: str | Path | None = None,
) -> str:
    """Send a text message and an optional local image to Gemini."""
    clean_message = str(message or "").strip()
    if not clean_message and image_path is None:
        raise GeminiRequestError("Hãy nhập tin nhắn hoặc đính kèm ảnh trước khi gửi.")

    parts: list[dict[str, Any]] = []
    if image_path is not None:
        parts.append(_load_image_part(image_path))
    parts.append({"text": clean_message or "Describe this image in detail."})
    return _generate_content(parts=parts, api_key=api_key, model=model)


def describe_fashion_image(
    image_path: str | Path,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
) -> str:
    """Ask Gemini to turn one local fashion image into an AI-video prompt."""
    key = str(api_key or "").strip()
    if not key:
        raise GeminiRequestError("Chưa có Gemini API key. Hãy thêm key trong Settings.")

    return _generate_content(
        parts=[_load_image_part(image_path), {"text": FASHION_PROMPT_INSTRUCTION}],
        api_key=key,
        model=model,
    )


def write_fashion_product_copy(
    image_path: str | Path,
    product_name: str,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
) -> FashionProductCopy:
    """Create a Vietnamese product caption and exactly five hashtags from Gemini."""

    clean_name = str(product_name or "").strip()
    if not clean_name:
        raise GeminiRequestError("Thiếu tên sản phẩm để Gemini viết mô tả.")
    response_text = _generate_content(
        parts=[
            _load_image_part(image_path),
            {"text": FASHION_PRODUCT_COPY_INSTRUCTION.format(product_name=clean_name)},
        ],
        api_key=api_key,
        model=model,
    )
    return _parse_fashion_product_copy(response_text, clean_name)


def _load_image_part(image_path: str | Path) -> dict[str, Any]:
    """Load a local image as the inline-data part accepted by Gemini."""
    path = Path(image_path)
    if not path.is_file():
        raise GeminiRequestError("Không tìm thấy ảnh đã chọn.")

    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise GeminiRequestError("Không thể đọc ảnh đã chọn.") from exc

    if not image_bytes:
        raise GeminiRequestError("Tệp ảnh đang trống.")
    if len(image_bytes) > MAX_INLINE_IMAGE_BYTES:
        raise GeminiRequestError("Ảnh quá lớn. Hãy chọn ảnh nhỏ hơn 14 MB.")

    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if not mime_type.startswith("image/"):
        raise GeminiRequestError("Vui lòng chọn một tệp ảnh hợp lệ.")

    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }
    }


def _generate_content(*, parts: list[dict[str, Any]], api_key: str, model: str) -> str:
    key = str(api_key or "").strip()
    if not key:
        raise GeminiRequestError("Chưa có Gemini API key. Hãy thêm key trong Settings.")

    model_id = str(model or DEFAULT_GEMINI_MODEL).strip()
    if model_id not in {item[0] for item in GEMINI_MODELS}:
        raise GeminiRequestError("Model Gemini không hợp lệ.")

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": 2048},
    }
    url = _API_URL.format(model=model_id)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = _read_http_error(exc)
        raise GeminiRequestError(detail) from exc
    except URLError as exc:
        raise GeminiRequestError("Không thể kết nối Gemini. Hãy kiểm tra kết nối mạng.") from exc
    except TimeoutError as exc:
        raise GeminiRequestError("Gemini mất quá nhiều thời gian phản hồi. Hãy thử lại.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GeminiRequestError("Không thể đọc phản hồi từ Gemini. Hãy thử lại.") from exc

    text = _extract_response_text(response_data)
    if not text:
        raise GeminiRequestError("Gemini không trả về prompt. Hãy thử lại với ảnh khác.")
    return text


def _extract_response_text(response_data: dict[str, Any]) -> str:
    candidates = response_data.get("candidates") or []
    for candidate in candidates:
        parts = ((candidate.get("content") or {}).get("parts") or [])
        text_parts = [str(part.get("text", "")).strip() for part in parts if part.get("text")]
        text = "\n".join(part for part in text_parts if part)
        if text:
            return text
    return ""


def _parse_fashion_product_copy(response_text: str, product_name: str) -> FashionProductCopy:
    text = str(response_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeminiRequestError("Gemini trả về mô tả không đúng định dạng. Hãy thử lại.") from exc
    if not isinstance(payload, dict):
        raise GeminiRequestError("Gemini trả về mô tả không đúng định dạng. Hãy thử lại.")

    caption = str(payload.get("caption") or "").strip()
    raw_hashtags = payload.get("hashtags")
    if not caption or not isinstance(raw_hashtags, list):
        raise GeminiRequestError("Gemini chưa trả về đủ caption và hashtag. Hãy thử lại.")
    hashtags = []
    seen = set()
    for raw_tag in raw_hashtags:
        tag = str(raw_tag or "").strip().lstrip("#")
        tag = re.sub(r"\s+", "", tag)
        if not tag:
            continue
        tag = "#%s" % tag
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            hashtags.append(tag)
    if len(hashtags) != 5:
        raise GeminiRequestError("Gemini phải trả về đúng 5 hashtag. Hãy thử lại.")
    return FashionProductCopy(caption=caption, hashtags=tuple(hashtags))


def _read_http_error(error: HTTPError) -> str:
    """Convert Gemini's HTTP error into a short message without exposing the API key."""
    try:
        payload = json.loads(error.read().decode("utf-8"))
        message = str((payload.get("error") or {}).get("message") or "").strip()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        message = ""

    if error.code in (401, 403):
        return "Gemini từ chối API key. Hãy kiểm tra key trong Settings."
    if error.code == 429:
        return "Gemini đang giới hạn lượt gọi. Hãy chờ một lúc rồi thử lại."
    if message:
        return f"Gemini: {message}"
    return f"Gemini trả về lỗi HTTP {error.code}."
