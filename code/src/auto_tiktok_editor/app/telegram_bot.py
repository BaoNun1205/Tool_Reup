"""Telegram polling bot that feeds queued single-item jobs into the editor pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import threading
import time
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

from auto_tiktok_editor.app.media_cleanup import cleanup_media_storage, format_cleanup_report
from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.runtime import ensure_local_telegram_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager
from auto_tiktok_editor.tiktok_profiles.telegram_queue import (
    copy_rendered_video_to_queue,
    enqueue_telegram_video,
    enqueue_telegram_video_draft,
    profile_slug_from_config,
)


TIKTOK_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TELEGRAM_API_RETRY_ATTEMPTS = 3
TELEGRAM_API_RETRY_BASE_DELAY_SECONDS = 2.0
TELEGRAM_JOB_RETRY_ATTEMPTS = 3
TELEGRAM_INPUT_BATCH_SECONDS = 10.0
TELEGRAM_PRODUCT_URL_RESOLVE_ATTEMPTS = 5
try:
    TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS = max(1, int(os.getenv("AUTO_EDITOR_TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS", "3")))
except ValueError:
    TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS = 3
STATE_SELECT_OPTION = "SELECT_OPTION"
STATE_WAIT_IMAGE = "WAIT_IMAGE"
STATE_WAIT_VIDEO_LINK = "WAIT_VIDEO_LINK"
STATE_WAIT_PRODUCT_LINK = "WAIT_PRODUCT_LINK"
STATE_WAIT_SCHEDULE_TIME = "WAIT_SCHEDULE_TIME"
STATE_CONFIRM = "CONFIRM"
TELEGRAM_OPTION_SPECS = {
    1: {
        "label": "Ảnh + Video + Sản phẩm + Giờ đăng",
        "requires_product": True,
        "requires_schedule": True,
    },
    2: {
        "label": "Ảnh + Video + Sản phẩm",
        "requires_product": True,
        "requires_schedule": False,
    },
    3: {
        "label": "Ảnh + Video",
        "requires_product": False,
        "requires_schedule": False,
    },
}
TIKTOK_DOWNLOAD_RETRY_MARKERS = (
    "download",
    "tiktok",
    "lazy-down",
    "yt-dlp",
    "source video",
    "playable video stream",
    "non-video artifacts",
)
CAPTION_KEY_RE = re.compile(r"^\s*(v|video|p|product|t|time)\s*:\s*(.*?)\s*$", re.IGNORECASE)
NATURAL_URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>()]+|(?:[a-z0-9-]+\.)+(?:com|vn|net|org|shop|co|io|me|ly|app|store|sg|id|th|ph|my|xyz)(?:/[^\s<>()]*)?)"
)


def parse_telegram_caption(caption: str) -> Dict[str, object]:
    parsed = {
        "video_link": None,
        "product_link": None,
        "schedule_time": None,
        "unknown_keys": [],
    }
    aliases = {
        "v": "video_link",
        "video": "video_link",
        "p": "product_link",
        "product": "product_link",
        "t": "schedule_time",
        "time": "schedule_time",
    }
    for line in (caption or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = CAPTION_KEY_RE.match(stripped)
        if not match:
            if ":" in stripped:
                parsed["unknown_keys"].append(stripped.split(":", 1)[0].strip())
            continue
        key, value = match.groups()
        field_name = aliases.get(key.lower())
        if field_name:
            parsed[field_name] = value.strip() or None
    return parsed


def extract_urls_from_caption(caption: str) -> List[str]:
    urls = []
    for match in NATURAL_URL_RE.finditer(caption or ""):
        raw_url = (match.group(1) or "").strip().rstrip(".,;!?)]]}>'\"")
        if not raw_url:
            continue
        if raw_url.lower().startswith("www."):
            raw_url = "https://%s" % raw_url
        elif not re.match(r"(?i)^https?://", raw_url):
            raw_url = "https://%s" % raw_url
        urls.append(raw_url)
    return urls


def extract_schedule_time_from_caption(caption: str, urls: List[str]) -> Optional[datetime]:
    text = NATURAL_URL_RE.sub(" ", caption or "")
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, order in (
        (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T]+(\d{1,2}):(\d{2})\b", "ymd"),
        (r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})[ T]+(\d{1,2}):(\d{2})\b", "dmy"),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        a, b, c, hour, minute = [int(part) for part in match.groups()]
        if order == "ymd":
            year, month, day = a, b, c
        else:
            day, month, year = a, b, c
        try:
            return datetime(year, month, day, hour, minute)
        except ValueError:
            return None
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if match:
        return _nearest_time_from_parts(int(match.group(1)), int(match.group(2)))
    match = re.search(r"\b(\d{1,2})h(?:\s*(\d{1,2}))?\b", text, flags=re.IGNORECASE)
    if match:
        return _nearest_time_from_parts(int(match.group(1)), int(match.group(2) or 0))
    return None


def parse_natural_telegram_caption(caption: str) -> Dict[str, object]:
    legacy = parse_telegram_caption(caption)
    has_legacy_keys = any(legacy.get(key) for key in ("video_link", "product_link", "schedule_time"))
    urls = extract_urls_from_caption(caption)
    warnings = []
    if len(urls) > 2:
        warnings.append("Phát hiện nhiều hơn 2 link, chỉ dùng 2 link đầu tiên.")
    schedule_dt = extract_schedule_time_from_caption(caption, urls)
    text_without_urls = NATURAL_URL_RE.sub(" ", caption or "")
    has_time_like_text = bool(
        re.search(r"\b\d{1,2}[:h]\d{0,2}\b", text_without_urls, flags=re.IGNORECASE)
        or re.search(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", text_without_urls)
    )
    data = {
        "option": 0,
        "image": None,
        "video_link": legacy.get("video_link") if has_legacy_keys else (urls[0] if len(urls) >= 1 else None),
        "product_link": legacy.get("product_link") if has_legacy_keys else (urls[1] if len(urls) >= 2 else None),
        "schedule_time": schedule_dt.isoformat(sep=" ", timespec="minutes") if schedule_dt else None,
        "schedule_time_error": bool(has_time_like_text and schedule_dt is None),
        "warnings": warnings,
        "urls": urls,
    }
    legacy_schedule = legacy.get("schedule_time")
    if legacy_schedule:
        data["schedule_time"] = _parse_telegram_schedule_value(str(legacy_schedule))
        data["schedule_time_error"] = data["schedule_time"] is None
    data["option"] = detect_input_option(data)
    return data


def detect_input_option(data: Dict[str, object]) -> int:
    has_product = bool(str(data.get("product_link") or "").strip())
    has_schedule = bool(str(data.get("schedule_time") or "").strip())
    if has_product and has_schedule:
        return 1
    if has_product:
        return 2
    return 3


def validate_telegram_input(data: Dict[str, object]) -> List[str]:
    errors = []
    video_link = str(data.get("video_link") or "").strip()
    product_link = str(data.get("product_link") or "").strip()
    schedule_time = str(data.get("schedule_time") or "").strip()
    if not data.get("image"):
        errors.append("Vui lòng gửi ảnh sản phẩm kèm caption.")
    if not video_link:
        errors.append("Thiếu link video. Hãy thêm dòng v: <link video>.")
    elif not _is_valid_http_url(video_link):
        errors.append("Link video không hợp lệ.")
    if product_link and not _is_valid_http_url(product_link):
        errors.append("Link sản phẩm không hợp lệ.")
    if data.get("schedule_time_error"):
        errors.append("Giờ đăng không hợp lệ.")
    if schedule_time:
        if not product_link:
            errors.append("Bạn đang nhập giờ đăng nhưng thiếu link sản phẩm. Vui lòng thêm link sản phẩm hoặc bỏ giờ đăng.")
        elif _parse_telegram_schedule_value(schedule_time) is None:
            errors.append("Giờ đăng không hợp lệ.")
    return errors


def _is_valid_http_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_telegram_schedule_value(value: str) -> Optional[str]:
    text = (value or "").strip()
    patterns = (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})$", "ymd"),
        (r"^(\d{4})/(\d{1,2})/(\d{1,2})[ T](\d{1,2}):(\d{2})$", "ymd"),
        (r"^(\d{1,2})-(\d{1,2})-(\d{4})[ T](\d{1,2}):(\d{2})$", "dmy"),
    )
    for pattern, order in patterns:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        a, b, c, hour, minute = [int(part) for part in match.groups()]
        if order == "ymd":
            year, month, day = a, b, c
        else:
            day, month, year = a, b, c
        return _normalize_telegram_schedule_parts(year, month, day, int(hour), int(minute))
    match = re.fullmatch(r"^(\d{1,2}):(\d{2})$", text)
    if match:
        now = datetime.now()
        return _normalize_telegram_schedule_parts(now.year, now.month, now.day, int(match.group(1)), int(match.group(2)))
    return None


def _nearest_time_from_parts(hour: int, minute: int) -> Optional[datetime]:
    now = datetime.now()
    try:
        value = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    if value <= now:
        value = value + timedelta(days=1)
    return value


def _normalize_telegram_schedule_parts(year: int, month: int, day: int, hour: int, minute: int) -> Optional[str]:
    try:
        value = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return value.isoformat(sep=" ", timespec="minutes")


def extract_tiktok_product_id(url: str) -> Optional[str]:
    parsed = urlparse((url or "").strip())
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith("tiktok.com"):
        return None

    product_path = parsed.path.rstrip("/")
    product_path_lower = product_path.casefold()
    has_product_path = any(
        marker in product_path_lower
        for marker in (
            "/view/product",
            "/pdp",
            "/product",
            "/shop/",
        )
    )
    query = parse_qs(parsed.query or "")
    query_keys = ["product_id", "productId", "product", "item_id", "itemId", "goods_id", "goodsId"]
    if has_product_path:
        query_keys.append("id")
    for key in query_keys:
        for value in query.get(key, []):
            match = re.search(r"\d{10,}", value or "")
            if match:
                return match.group(0)

    for pattern in (
        r"/view/product/(\d{10,})$",
        r"/(?:[^/?#]+/)?pdp(?:/[^/?#]*)*/(\d{10,})(?:\.html)?$",
        r"/(?:[^/?#]+/)?product(?:/[^/?#]*)*/(\d{10,})(?:\.html)?$",
        r"/(?:[^/?#]+/)?pdp/[^/?#]*?(\d{10,})(?:\.html)?$",
        r"/(?:[^/?#]+/)?product/[^/?#]*?(\d{10,})(?:\.html)?$",
    ):
        match = re.search(pattern, product_path)
        if match:
            return match.group(1)
    return None


def _is_short_tiktok_url(url: str) -> bool:
    hostname = (urlparse((url or "").strip()).hostname or "").lower()
    return hostname in {"vt.tiktok.com", "vm.tiktok.com"}


@dataclass
class TelegramQueuedJob:
    option: int
    source_video_url: str
    product_url: str
    product_id: str
    product_image_path: Path
    publish_mode: str = "now"
    scheduled_at: str = ""
    profile_video_id: Optional[int] = None
    profile_video_cut_mode: str = ""


@dataclass
class TelegramConversationState:
    workflow_state: str = STATE_SELECT_OPTION
    selected_option: Optional[int] = None
    draft_source_video_url: Optional[str] = None
    draft_product_url: Optional[str] = None
    draft_product_id: Optional[str] = None
    draft_product_image_path: Optional[Path] = None
    draft_publish_mode: Optional[str] = None
    draft_scheduled_at: str = ""
    draft_errors: List[str] = field(default_factory=list)
    queued_jobs: List[TelegramQueuedJob] = field(default_factory=list)
    processing: bool = False
    pending_timer: Optional[threading.Timer] = None


@dataclass
class TelegramJobResult:
    ok: bool
    final_video_path: Optional[Path] = None
    session_dir: Optional[Path] = None
    source_title: Optional[str] = None
    error: Optional[str] = None
    session_id: Optional[str] = None


class TelegramBotClient(object):
    def __init__(self, token: str, logger: Optional[logging.Logger] = None):
        self.token = token.strip()
        self.logger = logger or logging.getLogger("auto_tiktok_editor.telegram")
        if not self.token:
            raise ValueError("Cần cấu hình AUTO_EDITOR_TELEGRAM_BOT_TOKEN để chạy bot Telegram.")
        self.api_root = "https://api.telegram.org/bot%s" % self.token
        self.file_root = "https://api.telegram.org/file/bot%s" % self.token

    def get_updates(self, offset: Optional[int], timeout_seconds: int):
        payload = {"timeout": max(1, int(timeout_seconds))}
        if offset is not None:
            payload["offset"] = int(offset)
        try:
            return self._call_json_api(
                "getUpdates",
                payload=payload,
                timeout=timeout_seconds + 10,
                retry_attempts=1,
            )
        except RuntimeError as exc:
            self.logger.info("Telegram getUpdates skipped after transient polling error: %s", exc)
            return []

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[Dict[str, object]] = None) -> None:
        payload = {
            "chat_id": str(chat_id),
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self._call_json_api("sendMessage", payload=payload, timeout=30)

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload = {"callback_query_id": str(callback_query_id)}
        if text:
            payload["text"] = text
        self._call_json_api("answerCallbackQuery", payload=payload, timeout=3, retry_attempts=1)

    def send_document(
        self,
        chat_id: int,
        document_path: Path,
        caption: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> None:
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        self._call_multipart_api(
            "sendDocument",
            fields=fields,
            file_field="document",
            file_path=document_path,
            upload_filename=filename,
            timeout=300,
        )

    def send_video(self, chat_id: int, video_path: Path, caption: Optional[str] = None) -> None:
        self.send_document(chat_id, video_path, caption=caption, filename="video_final.mp4")

    def download_file(self, file_id: str, destination_dir: Path, preferred_name: Optional[str] = None) -> Path:
        file_info = self._call_json_api("getFile", payload={"file_id": file_id}, timeout=30)
        file_path = str(file_info.get("file_path") or "").strip()
        if not file_path:
            raise RuntimeError("Telegram không trả về đường dẫn file hợp lệ để tải xuống.")
        destination_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_path).suffix or Path(preferred_name or "").suffix or ".bin"
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(preferred_name or file_id).stem).strip("._") or file_id
        local_path = destination_dir / ("%s%s" % (safe_stem, suffix))
        request = Request("%s/%s" % (self.file_root, file_path))
        file_bytes = self._urlopen_bytes_with_retry(
            request,
            timeout=120,
            operation_label="Telegram file download",
            method="downloadFile",
        )
        local_path.write_bytes(file_bytes)
        return local_path

    def _call_json_api(
        self,
        method: str,
        payload: Optional[Dict[str, object]] = None,
        timeout: int = 30,
        retry_attempts: int = TELEGRAM_API_RETRY_ATTEMPTS,
    ):
        encoded = urlencode(payload or {}).encode("utf-8")
        request = Request("%s/%s" % (self.api_root, method), data=encoded)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        data = self._urlopen_json_with_retry(
            request,
            timeout=timeout,
            operation_label="Telegram API request",
            method=method,
            retry_attempts=retry_attempts,
        )
        if not data.get("ok"):
            raise RuntimeError("Telegram API returned an error for %s: %s" % (method, data))
        return data.get("result")

    def _call_multipart_api(
        self,
        method: str,
        fields: Dict[str, str],
        file_field: str,
        file_path: Path,
        upload_filename: Optional[str] = None,
        timeout: int = 300,
    ) -> None:
        boundary = "----AutoTelegram%s" % uuid.uuid4().hex
        body = bytearray()
        for key, value in fields.items():
            body.extend(("--%s\r\n" % boundary).encode("utf-8"))
            body.extend(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body.extend(("--%s\r\n" % boundary).encode("utf-8"))
        body.extend(
            (
                'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (file_field, upload_filename or file_path.name)
            ).encode("utf-8")
        )
        body.extend(("Content-Type: %s\r\n\r\n" % mime_type).encode("utf-8"))
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
        body.extend(("--%s--\r\n" % boundary).encode("utf-8"))
        request = Request("%s/%s" % (self.api_root, method), data=bytes(body))
        request.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
        data = self._urlopen_json_with_retry(
            request,
            timeout=timeout,
            operation_label="Telegram upload",
            method=method,
            retry_attempts=1,
        )
        if not data.get("ok"):
            raise RuntimeError("Telegram upload returned an error for %s: %s" % (method, data))

    def _urlopen_json_with_retry(
        self,
        request: Request,
        timeout: int,
        operation_label: str,
        method: str,
        retry_attempts: int = TELEGRAM_API_RETRY_ATTEMPTS,
    ):
        last_error = None
        retry_attempts = max(1, int(retry_attempts))
        for attempt in range(1, retry_attempts + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raise RuntimeError("%s failed for %s: %s" % (operation_label, method, exc))
            except (URLError, TimeoutError, ConnectionResetError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt >= retry_attempts:
                    break
                delay_seconds = TELEGRAM_API_RETRY_BASE_DELAY_SECONDS * attempt
                self.logger.warning(
                    "%s failed for %s on attempt %s/%s: %s. Retrying in %.1fs.",
                    operation_label,
                    method,
                    attempt,
                    retry_attempts,
                    exc,
                    delay_seconds,
                )
                time.sleep(delay_seconds)
        raise RuntimeError("%s failed for %s after %s attempts: %s" % (
            operation_label,
            method,
            retry_attempts,
            last_error,
        ))

    def _urlopen_bytes_with_retry(self, request: Request, timeout: int, operation_label: str, method: str) -> bytes:
        last_error = None
        for attempt in range(1, TELEGRAM_API_RETRY_ATTEMPTS + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    return response.read()
            except HTTPError as exc:
                raise RuntimeError("%s failed for %s: %s" % (operation_label, method, exc))
            except (URLError, TimeoutError, ConnectionResetError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt >= TELEGRAM_API_RETRY_ATTEMPTS:
                    break
                delay_seconds = TELEGRAM_API_RETRY_BASE_DELAY_SECONDS * attempt
                self.logger.warning(
                    "%s failed for %s on attempt %s/%s: %s. Retrying in %.1fs.",
                    operation_label,
                    method,
                    attempt,
                    TELEGRAM_API_RETRY_ATTEMPTS,
                    exc,
                    delay_seconds,
                )
                time.sleep(delay_seconds)
        raise RuntimeError("%s failed for %s after %s attempts: %s" % (
            operation_label,
            method,
            TELEGRAM_API_RETRY_ATTEMPTS,
            last_error,
        ))


class TelegramJobRunner(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        orchestrator: Optional[SessionOrchestrator] = None,
        license_checkpoint=None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor.telegram")
        self.license_checkpoint = license_checkpoint
        self.orchestrator = orchestrator or SessionOrchestrator(
            config=self.config,
            license_checkpoint=license_checkpoint,
            logger=self.logger,
        )

    def run(
        self,
        chat_id: int,
        source_video_url: str,
        product_image_path: Path,
        video_cut_mode: str | None = None,
    ) -> TelegramJobResult:
        render_config = self.config
        orchestrator = self.orchestrator
        requested_cut_mode = str(video_cut_mode or "").strip().lower()
        if requested_cut_mode and requested_cut_mode != str(getattr(self.config, "video_cut_mode", "") or "").strip().lower():
            render_config = replace(self.config, video_cut_mode=requested_cut_mode)
            orchestrator = SessionOrchestrator(
                config=render_config,
                license_checkpoint=self.license_checkpoint,
                logger=self.logger,
            )
        session_spec = SessionSpec(
            items=[
                SessionItemSpec(
                    row_id="chat_%s" % chat_id,
                    source_video_url=source_video_url,
                    product_image=Path(product_image_path),
                )
            ],
            output_root_dir=render_config.default_output_root,
            session_name="telegram_%s" % chat_id,
            cookies_file=None,
        )
        session_result = orchestrator.run(session_spec)
        if not session_result.items:
            return TelegramJobResult(
                ok=False,
                error="Pipeline không trả về item nào cho yêu cầu Telegram này.",
                session_dir=session_result.artifacts.session_dir if session_result.artifacts else None,
                session_id=session_result.session_id,
            )
        item_result = session_result.items[0]
        if item_result.status != "completed":
            return TelegramJobResult(
                ok=False,
                error=item_result.error or "Pipeline không thể hoàn tất video này.",
                session_dir=session_result.artifacts.session_dir if session_result.artifacts else None,
                session_id=session_result.session_id,
            )
        return TelegramJobResult(
            ok=True,
            final_video_path=item_result.artifacts.final_video_path,
            session_dir=session_result.artifacts.session_dir if session_result.artifacts else None,
            source_title=str(item_result.metadata.get("source_title") or "").strip() or None,
            session_id=session_result.session_id,
        )


class TelegramBotService(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        client: Optional[TelegramBotClient] = None,
        job_runner: Optional[TelegramJobRunner] = None,
        runtime_checkpoint=None,
        logger: Optional[logging.Logger] = None,
        executor: Optional[ThreadPoolExecutor] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        ensure_local_telegram_allowed(self.config, surface="telegram-bot-service")
        self.logger = logger or logging.getLogger("auto_tiktok_editor.telegram")
        self.client = client or TelegramBotClient(self.config.telegram_bot_token, logger=self.logger)
        self.runtime_checkpoint = runtime_checkpoint
        self.job_runner = job_runner or TelegramJobRunner(
            self.config,
            license_checkpoint=runtime_checkpoint,
            logger=self.logger,
        )
        self.executor = executor or ThreadPoolExecutor(
            max_workers=max(1, int(self.config.max_parallel_session_items)),
            thread_name_prefix="telegram-job",
        )
        self.input_mode = self._normalize_input_mode(os.getenv("AUTO_EDITOR_TELEGRAM_INPUT_MODE", "simple"))
        self._chat_states = {}  # type: Dict[int, TelegramConversationState]
        self._recent_chat_ids: List[int] = []
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_auto_cleanup_at = 0.0
        self._poll_offset_path = Path(self.config.telegram_input_root) / "_telegram_poll_offset.txt"
        pause_file = str(os.getenv("AUTO_EDITOR_TELEGRAM_PAUSE_FILE") or "").strip()
        self._pause_file_path = Path(pause_file) if pause_file else None

    def serve_forever(self) -> None:
        offset = self._load_poll_offset()
        self.logger.info("Telegram bot worker started.")
        while not self._stop_event.is_set():
            if self._is_pause_requested():
                time.sleep(max(1, int(self.config.telegram_poll_interval_seconds)))
                continue
            if self.runtime_checkpoint is not None:
                try:
                    self.runtime_checkpoint()
                except Exception as exc:
                    self.logger.error("Telegram bot stopped because the runtime license check failed: %s", exc)
                    break
            try:
                updates = self.client.get_updates(offset=offset, timeout_seconds=self.config.telegram_poll_timeout_seconds)
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = int(update_id) + 1
                        self._save_poll_offset(offset)
                    self.handle_update(update)
                self._maybe_cleanup_expired_media()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.logger.exception("Telegram polling loop failed: %s", exc)
                if self._stop_event.is_set():
                    break
                time.sleep(max(1, int(self.config.telegram_poll_interval_seconds)))

    def stop(self) -> None:
        self._stop_event.set()

    def _is_pause_requested(self) -> bool:
        return bool(self._pause_file_path and self._pause_file_path.exists())

    def _load_poll_offset(self) -> Optional[int]:
        try:
            if self._poll_offset_path.exists():
                value = self._poll_offset_path.read_text(encoding="utf-8").strip()
                if value:
                    return int(value)
        except (OSError, ValueError) as exc:
            self.logger.warning("Could not load Telegram poll offset from %s: %s", self._poll_offset_path, exc)
        return None

    def _save_poll_offset(self, offset: int) -> None:
        try:
            self._poll_offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._poll_offset_path.write_text(str(int(offset)), encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Could not save Telegram poll offset to %s: %s", self._poll_offset_path, exc)

    def handle_update(self, update: Dict[str, object]) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)
            return
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_id = int(chat_id)
        self._remember_chat_id(chat_id)
        if not self._is_chat_allowed(chat_id):
            self.client.send_message(chat_id, "Chat này chưa được cấp quyền dùng bot.")
            return
        text_value = str(message.get("text") or message.get("caption") or "").strip()
        command = text_value.lower()
        if command.startswith("/start") or command.startswith("/add") or command.startswith("/new"):
            self._reset_draft(chat_id)
            self.client.send_message(chat_id, self._instruction_text())
            return
        if command in {"cancel", "huy", "hủy", "/cancel"}:
            self._reset_draft(chat_id)
            self.client.send_message(chat_id, "Đã hủy nhập video.")
            return
        if text_value.startswith("/myid"):
            self.client.send_message(chat_id, self._chat_identity_text(message))
            return
        if text_value.startswith("/reset"):
            with self._state_lock:
                state = self._chat_states.pop(chat_id, None)
                if state is not None and state.pending_timer is not None:
                    state.pending_timer.cancel()
            self.client.send_message(chat_id, "Đã xoá dữ liệu tạm và hàng đợi của chat này.")
            return

        if text_value.startswith("/cleanup"):
            if self.has_processing_jobs():
                self.client.send_message(chat_id, "Dang co job Telegram duoc xu ly. Hay doi xong roi thu lai lenh /cleanup.")
                return
            self.clear_pending_jobs()
            report = cleanup_media_storage(self.config)
            self.client.send_message(chat_id, "%s Hang doi Telegram cung da duoc lam moi." % format_cleanup_report(report))
            return

        self._handle_direct_caption_message(chat_id, message)
        return

    def _handle_callback_query(self, callback_query: Dict[str, object]) -> None:
        callback_id = str(callback_query.get("id") or "")
        message = callback_query.get("message") or {}
        chat = message.get("chat") if isinstance(message, dict) else {}
        chat_id = (chat or {}).get("id")
        if chat_id is None:
            return
        chat_id = int(chat_id)
        self._remember_chat_id(chat_id)
        if not self._is_chat_allowed(chat_id):
            self.client.send_message(chat_id, "Chat này chưa được cấp quyền dùng bot.")
            return
        self._answer_callback_query_async(callback_id)
        data = str(callback_query.get("data") or "")
        if data.startswith("add_video_option:"):
            self.client.send_message(chat_id, self._instruction_text())
            return
        if data == "draft_confirm":
            self._confirm_draft(chat_id)
            return
        if data == "draft_cancel":
            self._reset_draft(chat_id)
            self.client.send_message(chat_id, "Đã hủy nhập video.")
            return
        if data == "draft_back":
            self._go_back(chat_id)
            return
        self.client.send_message(chat_id, self._instruction_text())

    def _answer_callback_query_async(self, callback_id: str) -> None:
        if not callback_id or not hasattr(self.client, "answer_callback_query"):
            return

        def worker() -> None:
            try:
                self.client.answer_callback_query(callback_id)
            except Exception as exc:
                self.logger.debug("Could not answer callback query: %s", exc)

        thread = threading.Thread(target=worker, name="telegram-callback-ack", daemon=True)
        thread.start()

    def _handle_direct_caption_message(self, chat_id: int, message: Dict[str, object]) -> None:
        caption = str(message.get("caption") or message.get("text") or "").strip()
        if not caption:
            self.client.send_message(chat_id, self._format_caption_error(["Thiếu caption. Vui lòng gửi caption có ít nhất 1 link video."]))
            return

        input_data = parse_natural_telegram_caption(caption)
        has_uploaded_image = self._message_has_image(message)
        main_image_path, auto_use_main_image = self._profile_main_image_for_current_bot()
        if auto_use_main_image and main_image_path is None:
            self.client.send_message(chat_id, "Profile đang bật Auto dùng Main Image nhưng Main Image chưa được thiết lập hoặc không còn tồn tại.")
            return
        use_main_image = bool(
            main_image_path
            and (auto_use_main_image or (not has_uploaded_image and bool(input_data.get("product_link"))))
        )
        input_data["image"] = main_image_path if use_main_image else has_uploaded_image
        errors = validate_telegram_input(input_data)
        input_data["option"] = detect_input_option(input_data)
        if errors:
            self.client.send_message(chat_id, self._format_caption_error(errors))
            return

        self.client.send_message(
            chat_id,
            "Đã nhận link, bot sẽ dùng Main Image của Profile và đưa vào hàng đợi..."
            if use_main_image
            else "Đã nhận format, bot đang tải ảnh và đưa vào hàng đợi...",
        )
        try:
            image_path = main_image_path if use_main_image else self._download_image_from_message(chat_id, message)
        except Exception as exc:
            self.logger.warning("Could not download Telegram image for chat %s: %s", chat_id, exc)
            self.client.send_message(chat_id, "Không tải được ảnh từ Telegram. Hãy gửi lại ảnh. Lỗi: %s" % exc)
            return
        if image_path is None:
            self.client.send_message(chat_id, "Vui lòng gửi ảnh sản phẩm kèm caption.")
            return

        input_data["image"] = image_path
        self.save_telegram_video_input(chat_id, input_data)
        self.client.send_message(chat_id, self._direct_input_success_text(input_data))
        self._maybe_start_job(chat_id)

    def _message_has_image(self, message: Dict[str, object]) -> bool:
        photo_list = message.get("photo")
        if isinstance(photo_list, list) and photo_list:
            return True
        document = message.get("document")
        if not isinstance(document, dict):
            return False
        mime_type = str(document.get("mime_type") or "").lower()
        return bool(mime_type.startswith("image/"))

    def _profile_main_image_for_current_bot(self) -> tuple[Optional[Path], bool]:
        profile_slug = str(getattr(self.config, "tiktok_profile_slug", "") or "").strip()
        if not profile_slug:
            return None, False
        try:
            manager = TikTokProfileManager()
            account = manager.find_account_for_profile_slug(profile_slug)
            if account is None:
                return None, False
            return manager.resolve_account_main_image_path(account), bool(account.auto_use_main_image)
        except Exception as exc:
            self.logger.warning("Could not resolve Main Image for Telegram profile %s: %s", profile_slug, exc)
            return None, False

    def save_telegram_video_input(self, chat_id: int, data: Dict[str, object]) -> None:
        product_url = str(data.get("product_link") or "").strip()
        product_id = extract_tiktok_product_id(product_url) or ""
        scheduled_at = str(data.get("schedule_time") or "").strip()
        queued_job = self._prepare_profile_queued_job(
            chat_id,
            TelegramQueuedJob(
                option=int(data.get("option") or 0),
                source_video_url=str(data.get("video_link") or "").strip(),
                product_url=product_url,
                product_id=product_id,
                product_image_path=Path(data.get("image")),
                publish_mode="scheduled" if scheduled_at else "now",
                scheduled_at=scheduled_at,
            ),
        )
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                state = TelegramConversationState()
                self._chat_states[chat_id] = state
            state.queued_jobs.append(queued_job)

    def _prepare_profile_queued_job(self, chat_id: int, queued_job: TelegramQueuedJob) -> TelegramQueuedJob:
        if not self._should_save_profile_draft() or queued_job.profile_video_id is not None:
            return queued_job
        profile_video = self._queue_telegram_video_draft(
            chat_id,
            queued_job.source_video_url,
            queued_job.product_url,
            queued_job.product_id,
            queued_job.product_image_path,
            queued_job.publish_mode,
            queued_job.scheduled_at,
            announce=False,
        )
        if profile_video is None:
            return queued_job
        try:
            profile_manager = TikTokProfileManager()
            profile_manager.update_video_status(profile_video.id, "queued", note=profile_video.note)
            profile_manager.add_log(
                "info",
                "telegram_render_queued",
                "Queued Telegram draft video %s for rendering." % profile_video.id,
                account_id=profile_video.account_id,
                video_id=profile_video.id,
            )
        except Exception as exc:
            self.logger.warning("Could not mark Telegram draft as queued: %s", exc)
        queued_job.profile_video_id = profile_video.id
        queued_job.profile_video_cut_mode = profile_video.cut_mode
        return queued_job

    def _format_caption_error(self, errors: List[str]) -> str:
        return "\n".join(
            [
                "❌ Sai format",
                "",
                *errors,
                "",
                "Vui lòng gửi ảnh sản phẩm kèm caption:",
                "",
                "- 1 link: link video",
                "- 2 link: link video + link sản phẩm",
                "- Thêm giờ đăng nếu cần",
                "",
                "Ví dụ:",
                "https://vt.tiktok.com/abc123",
                "https://shopee.vn/product/xyz",
                "22:30",
            ]
        )

    def _direct_input_success_text(self, data: Dict[str, object]) -> str:
        option = int(data.get("option") or 0)
        option_label = {1: "Full", 2: "No schedule", 3: "Basic"}.get(option, "Basic")
        product_link = str(data.get("product_link") or "").strip() or "Không có"
        schedule_time = str(data.get("schedule_time") or "").strip() or "Không có"
        lines = [
            "✅ Đã nhận dữ liệu",
            "",
            "Option: %s" % option_label,
            "Ảnh sản phẩm: OK",
            "Video: %s" % str(data.get("video_link") or "").strip(),
            "Sản phẩm: %s" % product_link,
            "Giờ đăng: %s" % schedule_time,
        ]
        for warning in data.get("warnings") or []:
            lines.append("Cảnh báo: %s" % warning)
        return "\n".join(lines)

    def _handle_workflow_message(self, chat_id: int, message: Dict[str, object], text_value: str) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            current_state = state.workflow_state if state is not None else STATE_SELECT_OPTION
        if current_state == STATE_WAIT_IMAGE:
            try:
                image_path = self._download_image_from_message(chat_id, message)
            except Exception as exc:
                self.logger.warning("Could not download Telegram image for chat %s: %s", chat_id, exc)
                self.client.send_message(chat_id, "Không tải được ảnh từ Telegram. Hãy gửi lại ảnh. Lỗi: %s" % exc)
                return
            if image_path is None:
                self._send_message(chat_id, "Bước này cần ảnh sản phẩm. Hãy gửi photo/image.", reply_markup=self._cancel_back_keyboard(False))
                return
            with self._state_lock:
                state = self._chat_states[chat_id]
                state.draft_product_image_path = image_path
                state.workflow_state = STATE_WAIT_VIDEO_LINK
            self._send_message(chat_id, "Đã nhận ảnh. Gửi link video TikTok.", reply_markup=self._cancel_back_keyboard(True))
            return
        if current_state == STATE_WAIT_VIDEO_LINK:
            video_url, _product_url, _product_id = self._classify_tiktok_urls(text_value)
            if not video_url:
                self._send_message(chat_id, "Link video không hợp lệ. Hãy gửi URL video TikTok.", reply_markup=self._cancel_back_keyboard(True))
                return
            with self._state_lock:
                state = self._chat_states[chat_id]
                state.draft_source_video_url = video_url
                if self._state_requires_product(state):
                    state.workflow_state = STATE_WAIT_PRODUCT_LINK
                    next_message = "Đã nhận link video. Gửi link sản phẩm."
                    next_keyboard = self._cancel_back_keyboard(True)
                elif self._state_requires_schedule(state):
                    state.workflow_state = STATE_WAIT_SCHEDULE_TIME
                    next_message = "Đã nhận link video. Gửi giờ đăng theo HH:MM hoặc YYYY-MM-DD HH:MM."
                    next_keyboard = self._cancel_back_keyboard(True)
                else:
                    state.workflow_state = STATE_CONFIRM
                    next_message = self._draft_summary_text(state)
                    next_keyboard = self._confirm_keyboard()
            self._send_message(chat_id, next_message, reply_markup=next_keyboard)
            return
        if current_state == STATE_WAIT_PRODUCT_LINK:
            product_url, product_id = self._parse_product_link(text_value)
            if not product_url:
                self._send_message(chat_id, "Link sản phẩm không hợp lệ. Hãy gửi một URL hợp lệ.", reply_markup=self._cancel_back_keyboard(True))
                return
            with self._state_lock:
                state = self._chat_states[chat_id]
                state.draft_product_url = product_url
                state.draft_product_id = product_id or ""
                if self._state_requires_schedule(state):
                    state.workflow_state = STATE_WAIT_SCHEDULE_TIME
                    next_message = "Đã nhận link sản phẩm. Gửi giờ đăng theo HH:MM hoặc YYYY-MM-DD HH:MM."
                    next_keyboard = self._cancel_back_keyboard(True)
                else:
                    state.workflow_state = STATE_CONFIRM
                    next_message = self._draft_summary_text(state)
                    next_keyboard = self._confirm_keyboard()
            self._send_message(chat_id, next_message, reply_markup=next_keyboard)
            return
        if current_state == STATE_WAIT_SCHEDULE_TIME:
            publish_mode, scheduled_at, schedule_error = self._parse_schedule_text(text_value)
            if schedule_error or publish_mode != "scheduled":
                self._send_message(
                    chat_id,
                    schedule_error or "Giờ đăng không hợp lệ. Gửi theo HH:MM hoặc YYYY-MM-DD HH:MM.",
                    reply_markup=self._cancel_back_keyboard(True),
                )
                return
            with self._state_lock:
                state = self._chat_states[chat_id]
                state.draft_publish_mode = publish_mode
                state.draft_scheduled_at = scheduled_at or ""
                state.workflow_state = STATE_CONFIRM
                summary = self._draft_summary_text(state)
            self._send_message(chat_id, summary, reply_markup=self._confirm_keyboard())
            return
        if current_state == STATE_CONFIRM:
            if text_value.lower() in {"confirm", "luu", "lưu", "save"}:
                self._confirm_draft(chat_id)
            else:
                self._send_message(chat_id, self._current_summary_for_chat(chat_id), reply_markup=self._confirm_keyboard())
            return
        self._send_option_menu(chat_id)

    def _send_option_menu(self, chat_id: int) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                self._chat_states[chat_id] = TelegramConversationState()
            else:
                self._clear_draft_fields(state)
                state.workflow_state = STATE_SELECT_OPTION
                state.selected_option = None
        self.client.send_message(chat_id, self._instruction_text())

    def _start_option_flow(self, chat_id: int, option: int) -> None:
        if option not in TELEGRAM_OPTION_SPECS:
            self._send_option_menu(chat_id)
            return
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                state = TelegramConversationState()
                self._chat_states[chat_id] = state
            self._clear_draft_fields(state)
            state.selected_option = option
            state.workflow_state = STATE_WAIT_IMAGE
        self._send_message(
            chat_id,
            "Option %s: %s\nBước 1: gửi ảnh sản phẩm." % (option, TELEGRAM_OPTION_SPECS[option]["label"]),
            reply_markup=self._cancel_back_keyboard(False),
        )

    def _confirm_draft(self, chat_id: int) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None or state.workflow_state != STATE_CONFIRM:
                send_menu = True
            else:
                send_menu = False
                enqueued = self._enqueue_ready_job(chat_id, state)
                if enqueued:
                    state.workflow_state = STATE_SELECT_OPTION
                    state.selected_option = None
                should_start_job = enqueued
        if send_menu:
            self._send_option_menu(chat_id)
            return
        if not enqueued:
            self._send_message(chat_id, "Dữ liệu chưa đủ. Hãy kiểm tra lại.", reply_markup=self._confirm_keyboard())
            return
        self.client.send_message(chat_id, "Đã nhận dữ liệu và thêm job vào hàng đợi xử lý. Video sẽ được lưu vào Profile Manager sau khi edit xong.")
        if should_start_job:
            self._maybe_start_job(chat_id)

    def _go_back(self, chat_id: int) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None or state.selected_option is None:
                target_state = STATE_SELECT_OPTION
            else:
                target_state = self._previous_state(state)
                state.workflow_state = target_state
                if target_state == STATE_WAIT_IMAGE:
                    state.draft_product_image_path = None
                elif target_state == STATE_WAIT_VIDEO_LINK:
                    state.draft_source_video_url = None
                elif target_state == STATE_WAIT_PRODUCT_LINK:
                    state.draft_product_url = None
                    state.draft_product_id = None
                elif target_state == STATE_WAIT_SCHEDULE_TIME:
                    state.draft_publish_mode = None
                    state.draft_scheduled_at = ""
        if target_state == STATE_SELECT_OPTION:
            self._send_option_menu(chat_id)
        elif target_state == STATE_WAIT_IMAGE:
            self._send_message(chat_id, "Quay lại bước ảnh sản phẩm. Hãy gửi ảnh.", reply_markup=self._cancel_back_keyboard(False))
        elif target_state == STATE_WAIT_VIDEO_LINK:
            self._send_message(chat_id, "Quay lại bước link video. Hãy gửi link video TikTok.", reply_markup=self._cancel_back_keyboard(True))
        elif target_state == STATE_WAIT_PRODUCT_LINK:
            self._send_message(chat_id, "Quay lại bước link sản phẩm. Hãy gửi link sản phẩm.", reply_markup=self._cancel_back_keyboard(True))
        else:
            self._send_message(chat_id, "Quay lại bước giờ đăng. Hãy gửi HH:MM hoặc YYYY-MM-DD HH:MM.", reply_markup=self._cancel_back_keyboard(True))

    def _reset_draft(self, chat_id: int) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                self._chat_states[chat_id] = TelegramConversationState()
                return
            if state.pending_timer is not None:
                state.pending_timer.cancel()
                state.pending_timer = None
            self._clear_draft_fields(state)
            state.workflow_state = STATE_SELECT_OPTION
            state.selected_option = None

    def _send_message(self, chat_id: int, text: str, reply_markup: Optional[Dict[str, object]] = None) -> None:
        if reply_markup is None:
            self.client.send_message(chat_id, text)
            return
        try:
            self.client.send_message(chat_id, text, reply_markup=reply_markup)
        except TypeError:
            self.client.send_message(chat_id, text)

    def _clear_draft_fields(self, state: TelegramConversationState) -> None:
        state.draft_source_video_url = None
        state.draft_product_url = None
        state.draft_product_id = None
        state.draft_product_image_path = None
        state.draft_publish_mode = None
        state.draft_scheduled_at = ""
        state.draft_errors = []

    def _option_keyboard(self) -> Dict[str, object]:
        return {
            "inline_keyboard": [
                [{"text": "Option 1: Ảnh + Video + Sản phẩm + Giờ đăng", "callback_data": "add_video_option:1"}],
                [{"text": "Option 2: Ảnh + Video + Sản phẩm", "callback_data": "add_video_option:2"}],
                [{"text": "Option 3: Ảnh + Video", "callback_data": "add_video_option:3"}],
            ]
        }

    def _cancel_back_keyboard(self, include_back: bool) -> Dict[str, object]:
        row = []
        if include_back:
            row.append({"text": "Quay lại", "callback_data": "draft_back"})
        row.append({"text": "Hủy", "callback_data": "draft_cancel"})
        return {"inline_keyboard": [row]}

    def _confirm_keyboard(self) -> Dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {"text": "Confirm/Lưu", "callback_data": "draft_confirm"},
                    {"text": "Cancel/Hủy", "callback_data": "draft_cancel"},
                ],
                [{"text": "Quay lại", "callback_data": "draft_back"}],
            ]
        }

    def _state_requires_product(self, state: TelegramConversationState) -> bool:
        option = int(state.selected_option or 0)
        return bool(TELEGRAM_OPTION_SPECS.get(option, {}).get("requires_product"))

    def _state_requires_schedule(self, state: TelegramConversationState) -> bool:
        option = int(state.selected_option or 0)
        return bool(TELEGRAM_OPTION_SPECS.get(option, {}).get("requires_schedule"))

    def _previous_state(self, state: TelegramConversationState) -> str:
        current = state.workflow_state
        if current == STATE_WAIT_VIDEO_LINK:
            return STATE_WAIT_IMAGE
        if current == STATE_WAIT_PRODUCT_LINK:
            return STATE_WAIT_VIDEO_LINK
        if current == STATE_WAIT_SCHEDULE_TIME:
            return STATE_WAIT_PRODUCT_LINK if self._state_requires_product(state) else STATE_WAIT_VIDEO_LINK
        if current == STATE_CONFIRM:
            if self._state_requires_schedule(state):
                return STATE_WAIT_SCHEDULE_TIME
            if self._state_requires_product(state):
                return STATE_WAIT_PRODUCT_LINK
            return STATE_WAIT_VIDEO_LINK
        return STATE_SELECT_OPTION

    def _parse_product_link(self, text_value: str) -> tuple[Optional[str], Optional[str]]:
        urls = TIKTOK_URL_RE.findall(text_value or "")
        if not urls:
            return None, None
        clean_url = urls[0].strip().rstrip(".,)]}")
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None, None
        if _is_short_tiktok_url(clean_url):
            resolved_url = self._resolve_tiktok_url(
                clean_url,
                attempts=TELEGRAM_PRODUCT_URL_RESOLVE_ATTEMPTS,
                require_product_id=True,
            )
            return resolved_url, extract_tiktok_product_id(resolved_url) or ""
        return clean_url, extract_tiktok_product_id(clean_url) or ""

    def _extract_product_id_after_edit(self, product_url: str) -> tuple[str, str]:
        clean_url = (product_url or "").strip()
        if not clean_url:
            return "", ""
        resolved_url = (
            self._resolve_tiktok_url(
                clean_url,
                attempts=TELEGRAM_PRODUCT_URL_RESOLVE_ATTEMPTS,
                require_product_id=True,
            )
            if _is_short_tiktok_url(clean_url)
            else clean_url
        )
        return resolved_url, extract_tiktok_product_id(resolved_url) or ""

    def _current_summary_for_chat(self, chat_id: int) -> str:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                return "Chưa có dữ liệu để lưu."
            return self._draft_summary_text(state)

    def _draft_summary_text(self, state: TelegramConversationState) -> str:
        option = int(state.selected_option or 0)
        option_label = TELEGRAM_OPTION_SPECS.get(option, {}).get("label", "Chưa chọn")
        product_value = state.draft_product_url if state.draft_product_url else "Không có"
        schedule_value = state.draft_scheduled_at if state.draft_scheduled_at else "Không có"
        return "\n".join(
            [
                "Tóm tắt dữ liệu:",
                "Option: %s - %s" % (option or "", option_label),
                "Ảnh sản phẩm: Có" if state.draft_product_image_path else "Ảnh sản phẩm: Chưa có",
                "Link video: %s" % (state.draft_source_video_url or "Chưa có"),
                "Link sản phẩm: %s" % product_value,
                "Giờ đăng: %s" % schedule_value,
                "",
                "Bấm Confirm/Lưu để lưu hoặc Cancel/Hủy để bỏ.",
            ]
        )

    def _schedule_pending_input_response_locked(self, chat_id: int, state: TelegramConversationState) -> None:
        if state.pending_timer is not None:
            return
        timer = threading.Timer(TELEGRAM_INPUT_BATCH_SECONDS, self._flush_pending_input, args=(chat_id,))
        timer.daemon = True
        state.pending_timer = timer
        timer.start()

    def _flush_pending_input(self, chat_id: int) -> None:
        response_text = ""
        should_start_job = False
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                return
            if state.pending_timer is not None:
                state.pending_timer.cancel()
            state.pending_timer = None
            enqueued_job = self._enqueue_ready_job(chat_id, state)
            response_text = self._compose_state_message(state=state, enqueued_job=enqueued_job)
            should_start_job = enqueued_job
        if response_text:
            self.client.send_message(chat_id, response_text)
        if should_start_job:
            self._maybe_start_job(chat_id)

    def _maybe_start_job(self, chat_id: int) -> None:
        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None or state.processing:
                return
            if not state.queued_jobs:
                return
            queued_job = state.queued_jobs.pop(0)
            state.processing = True
            waiting_count = len(state.queued_jobs)
        if waiting_count > 0:
            self.client.send_message(
                chat_id,
                "Bắt đầu xử lý job tiếp theo. Còn %s job đang chờ trong hàng đợi." % waiting_count,
            )
        else:
            self.client.send_message(chat_id, "Bắt đầu xử lý job này. Xong mình sẽ gửi video lại ngay.")
        self.executor.submit(
            self._run_job_and_reply,
            chat_id,
            queued_job.source_video_url,
            queued_job.product_url,
            queued_job.product_id,
            queued_job.product_image_path,
            queued_job.publish_mode,
            queued_job.scheduled_at,
            queued_job.profile_video_id,
            queued_job.profile_video_cut_mode,
        )

    def _run_job_and_reply(
        self,
        chat_id: int,
        source_video_url: str,
        product_url_or_image_path,
        product_id: str | None = None,
        product_image_path: Optional[Path] = None,
        publish_mode: str = "now",
        scheduled_at: str = "",
        profile_video_id: int | None = None,
        profile_video_cut_mode: str = "",
    ) -> None:
        if product_image_path is None:
            product_image_path = Path(product_url_or_image_path)
            product_id = product_id or ""
            product_url = ""
        else:
            product_url = str(product_url_or_image_path or "")
            product_image_path = Path(product_image_path)
            product_id = product_id or ""
        result = None
        profile_video = None
        profile_manager = None
        try:
            if self._should_save_profile_draft():
                profile_manager = TikTokProfileManager()
                if profile_video_id is not None:
                    profile_video = profile_manager.get_video(profile_video_id)
                    if profile_video is None:
                        self.client.send_message(chat_id, "Khong tim thay video da xep hang trong Profile Manager: %s" % profile_video_id)
                        return
                else:
                    profile_video = self._queue_telegram_video_draft(
                        chat_id,
                        source_video_url,
                        product_url,
                        product_id,
                        product_image_path,
                        publish_mode,
                        scheduled_at,
                    )
                    if profile_video is None:
                        return
                    profile_manager.update_video_status(profile_video.id, "queued", note=profile_video.note)
                    profile_manager.add_log(
                        "info",
                        "telegram_render_queued",
                        "Queued Telegram draft video %s for immediate rendering." % profile_video.id,
                        account_id=profile_video.account_id,
                        video_id=profile_video.id,
                    )
                    self.client.send_message(chat_id, "Da luu video vao profile va bat dau tao video.")
                profile_manager.update_video_status(profile_video.id, "rendering", note=profile_video.note)
                render_cut_mode = str(profile_video_cut_mode or profile_video.cut_mode or "original").strip().lower()
                result = self._run_job_with_processing_retries(
                    chat_id,
                    source_video_url,
                    product_image_path,
                    video_cut_mode=render_cut_mode,
                )
                if not result.ok or result.final_video_path is None or not result.final_video_path.exists():
                    error_text = result.error or "khong tim thay video dau ra."
                    profile_manager.update_video_status(profile_video.id, "error", note=error_text)
                    profile_manager.add_log(
                        "error",
                        "telegram_render_error",
                        "Telegram draft video %s render failed: %s" % (profile_video.id, error_text),
                        account_id=profile_video.account_id,
                        video_id=profile_video.id,
                    )
                    self.client.send_message(chat_id, "Job bi loi: %s" % error_text)
                    return
                stored_final_path = copy_rendered_video_to_queue(
                    profile_slug_from_config(self.config),
                    result.final_video_path,
                )
                updated_video = profile_manager.mark_video_rendered(
                    profile_video.id,
                    stored_final_path,
                    source_title=result.source_title,
                )
                profile_manager.add_log(
                    "info",
                    "telegram_render_completed",
                    "Rendered Telegram draft video %s with cut mode %s." % (profile_video.id, render_cut_mode),
                    account_id=updated_video.account_id,
                    video_id=updated_video.id,
                )
                self.client.send_message(chat_id, "Da tao xong video va luu vao profile.")
                return
            render_cut_mode = self._profile_cut_mode_for_current_bot()
            result = self._run_job_with_processing_retries(
                chat_id,
                source_video_url,
                product_image_path,
                video_cut_mode=render_cut_mode,
            )
            if not result.ok or result.final_video_path is None or not result.final_video_path.exists():
                self.client.send_message(
                    chat_id,
                    "Job bi loi: %s" % (result.error or "khong tim thay video dau ra."),
                )
                return
            caption = result.source_title or "Video đã edit xong."
            if self._effective_save_to_profile():
                self._queue_completed_telegram_video(
                    chat_id,
                    result,
                    source_video_url,
                    product_url,
                    product_id,
                    publish_mode,
                    scheduled_at,
                )
            if self._effective_send_result_to_telegram():
                self.client.send_document(
                    chat_id,
                    result.final_video_path,
                    caption=caption,
                    filename="video_final.mp4",
                )
                if product_id:
                    self.client.send_message(chat_id, product_id)
            if False:
                    self.client.send_message(chat_id, "Đã lưu video vào hàng đợi TikTok Profile Manager.")
        except Exception as exc:
            self.logger.exception("Telegram job failed for chat %s.", chat_id)
            if profile_video is not None:
                try:
                    (profile_manager or TikTokProfileManager()).update_video_status(profile_video.id, "error", note=str(exc))
                except Exception:
                    pass
            self.client.send_message(chat_id, "Co loi khi xu ly hoac gui job: %s" % exc)
        finally:
            if self.config.telegram_cleanup_after_job_enabled:
                self._cleanup_completed_job_files(product_image_path, result)
            with self._state_lock:
                state = self._chat_states.get(chat_id)
                if state is not None:
                    state.processing = False
                    has_follow_up = bool(state.queued_jobs)
                else:
                    has_follow_up = False
            if has_follow_up:
                self._maybe_start_job(chat_id)

    def _queue_completed_telegram_video(
        self,
        chat_id: int,
        result: TelegramJobResult,
        source_video_url: str,
        product_url: str,
        product_id: str,
        publish_mode: str,
        scheduled_at: str,
    ) -> bool:
        if result.final_video_path is None:
            return False
        if not str(getattr(self.config, "tiktok_profile_slug", "") or "").strip():
            self.client.send_message(chat_id, "Bot nay chua duoc map voi profile TikTok de luu video.")
            return False
        try:
            resolved_product_url = product_url
            resolved_product_id = product_id
            if resolved_product_url and not resolved_product_id:
                resolved_product_url, resolved_product_id = self._extract_product_id_after_edit(product_url)
            if resolved_product_url and not resolved_product_id:
                self.client.send_message(
                    chat_id,
                    "Da nhan link san pham nhung chua lay duoc TikTok product ID. Link: %s" % resolved_product_url,
                )
            queued = enqueue_telegram_video(
                self.config,
                result.final_video_path,
                result.source_title,
                source_video_url,
                resolved_product_id,
                product_url=resolved_product_url,
                publish_mode=publish_mode,
                scheduled_at=scheduled_at,
            )
            if queued:
                if resolved_product_id:
                    self.client.send_message(chat_id, "Da luu video va product ID %s vao profile." % resolved_product_id)
                else:
                    self.client.send_message(chat_id, "Da luu video vao profile.")
            return queued
        except Exception as exc:
            self.logger.warning("Could not queue Telegram result for TikTok Profile Manager: %s", exc)
            return False

    def _queue_telegram_video_draft(
        self,
        chat_id: int,
        source_video_url: str,
        product_url: str,
        product_id: str,
        product_image_path: Path,
        publish_mode: str,
        scheduled_at: str,
        announce: bool = True,
    ):
        if not str(getattr(self.config, "tiktok_profile_slug", "") or "").strip():
            if announce:
                self.client.send_message(chat_id, "Bot nay chua duoc map voi profile TikTok de luu video.")
            return None
        try:
            resolved_product_url = product_url
            resolved_product_id = product_id
            if resolved_product_url and not resolved_product_id:
                resolved_product_url, resolved_product_id = self._extract_product_id_after_edit(product_url)
            if resolved_product_url and not resolved_product_id:
                self.client.send_message(
                    chat_id,
                    "Da nhan link san pham nhung chua lay duoc TikTok product ID. Link: %s" % resolved_product_url,
                )
            queued = enqueue_telegram_video_draft(
                self.config,
                source_video_url=source_video_url,
                product_image_path=product_image_path,
                source_title=None,
                product_id=resolved_product_id,
                product_url=resolved_product_url,
                publish_mode=publish_mode,
                scheduled_at=scheduled_at,
            )
            if queued and announce:
                if resolved_product_id:
                    self.client.send_message(chat_id, "Da luu video va product ID %s vao profile." % resolved_product_id)
                else:
                    self.client.send_message(chat_id, "Da luu video vao profile.")
            return queued
        except Exception as exc:
            self.logger.warning("Could not queue Telegram draft for TikTok Profile Manager: %s", exc)
            if announce:
                self.client.send_message(chat_id, "Khong luu duoc draft vao Profile Manager: %s" % exc)
            return None

    def _should_save_profile_draft(self) -> bool:
        return (
            self._effective_save_to_profile()
            and bool(str(getattr(self.config, "tiktok_profile_slug", "") or "").strip())
            and not self._effective_send_result_to_telegram()
        )

    def _effective_save_to_profile(self) -> bool:
        save_to_profile = bool(getattr(self.config, "telegram_save_received_video_to_profile", True))
        send_result = bool(getattr(self.config, "telegram_send_result_to_telegram", False))
        if not save_to_profile and not send_result:
            return True
        return save_to_profile

    def _effective_send_result_to_telegram(self) -> bool:
        return bool(getattr(self.config, "telegram_send_result_to_telegram", False))

    def _profile_cut_mode_for_current_bot(self) -> str:
        profile_slug = str(getattr(self.config, "tiktok_profile_slug", "") or "").strip()
        if not profile_slug:
            return ""
        try:
            account = TikTokProfileManager().find_account_for_profile_slug(profile_slug)
        except Exception as exc:
            self.logger.warning("Could not load profile cut mode for bot/profile %s: %s", profile_slug, exc)
            return ""
        if account is None:
            self.logger.warning("No profile account found for bot/profile slug %s while resolving cut mode.", profile_slug)
            return ""
        cut_mode = str(getattr(account, "cut_mode", "") or "").strip().lower()
        if cut_mode:
            try:
                TikTokProfileManager().add_log(
                    "info",
                    "telegram_profile_cut_mode",
                    "Telegram job mapped to profile %s with cut mode %s." % (account.name, cut_mode),
                    account_id=account.id,
                )
            except Exception as exc:
                self.logger.debug("Could not write Telegram profile cut mode log: %s", exc)
            self.logger.info(
                "Telegram job for bot/profile %s will use profile %s cut mode: %s.",
                profile_slug,
                account.name,
                cut_mode,
            )
        return cut_mode

    def _run_job_with_processing_retries(
        self,
        chat_id: int,
        source_video_url: str,
        product_image_path: Path,
        video_cut_mode: str | None = None,
    ) -> TelegramJobResult:
        last_result = None
        for attempt in range(1, TELEGRAM_JOB_RETRY_ATTEMPTS + 1):
            try:
                result = self.job_runner.run(
                    chat_id,
                    source_video_url,
                    product_image_path,
                    video_cut_mode=video_cut_mode,
                )
            except Exception as exc:
                if attempt >= TELEGRAM_JOB_RETRY_ATTEMPTS or not self._should_retry_processing_error(str(exc)):
                    raise
                self.logger.warning(
                    "Telegram job attempt %s/%s failed for chat %s: %s",
                    attempt,
                    TELEGRAM_JOB_RETRY_ATTEMPTS,
                    chat_id,
                    exc,
                )
                self.client.send_message(
                    chat_id,
                    "Job loi o lan %s/%s, bot se tu thu lai: %s"
                    % (attempt, TELEGRAM_JOB_RETRY_ATTEMPTS, exc),
                )
                continue
            last_result = result
            output_ok = result.ok and result.final_video_path is not None and result.final_video_path.exists()
            if output_ok:
                return result
            error_text = result.error or "khong tim thay video dau ra"
            if not self._should_retry_processing_error(error_text):
                return result
            if attempt < TELEGRAM_JOB_RETRY_ATTEMPTS:
                self.client.send_message(
                    chat_id,
                    "Job loi o lan %s/%s, bot se tu lam lai: %s"
                    % (attempt, TELEGRAM_JOB_RETRY_ATTEMPTS, error_text),
                )
        return last_result or TelegramJobResult(ok=False, error="Pipeline khong tra ve ket qua.")

    def _should_retry_processing_error(self, error_text: str) -> bool:
        normalized = str(error_text or "").lower()
        return any(marker in normalized for marker in TIKTOK_DOWNLOAD_RETRY_MARKERS)

    def _cleanup_completed_job_files(self, product_image_path: Path, result: Optional[TelegramJobResult]) -> None:
        paths = []  # type: List[Path]
        if result is not None and result.session_dir is not None:
            paths.append(Path(result.session_dir))
        product_path = Path(product_image_path).expanduser().resolve()
        telegram_input_root = Path(self.config.telegram_input_root).expanduser().resolve()
        if product_path == telegram_input_root or telegram_input_root in product_path.parents:
            paths.append(product_path)
        for path in paths:
            try:
                self._delete_ephemeral_path(path)
            except Exception as exc:
                self.logger.warning("Could not clean Telegram job file %s: %s", path, exc)

    def _delete_ephemeral_path(self, path: Path) -> None:
        resolved = Path(path).expanduser().resolve()
        allowed_roots = (
            Path(self.config.default_output_root).expanduser().resolve(),
            Path(self.config.telegram_input_root).expanduser().resolve(),
        )
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            self.logger.warning("Skip cleanup outside Telegram storage roots: %s", resolved)
            return
        if not resolved.exists():
            return
        if resolved.is_dir():
            shutil.rmtree(str(resolved), ignore_errors=True)
        else:
            resolved.unlink()
            self._remove_empty_parent_dirs(resolved.parent, allowed_roots)

    def _remove_empty_parent_dirs(self, start_dir: Path, allowed_roots) -> None:
        current = Path(start_dir).expanduser().resolve()
        allowed_roots = tuple(Path(root).expanduser().resolve() for root in allowed_roots)
        while True:
            if current in allowed_roots:
                return
            if not any(root in current.parents for root in allowed_roots):
                return
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _download_image_from_message(self, chat_id: int, message: Dict[str, object]) -> Optional[Path]:
        photo_list = message.get("photo")
        if isinstance(photo_list, list) and photo_list:
            selected = photo_list[-1]
            file_id = selected.get("file_id")
            if file_id:
                return self.client.download_file(
                    str(file_id),
                    self._chat_input_dir(chat_id),
                    preferred_name="photo_%s.jpg" % str(file_id),
                )
        document = message.get("document")
        if not isinstance(document, dict):
            return None
        mime_type = str(document.get("mime_type") or "").lower()
        if mime_type and not mime_type.startswith("image/"):
            return None
        file_id = document.get("file_id")
        if not file_id:
            return None
        preferred_name = str(document.get("file_name") or ("document_%s" % file_id))
        return self.client.download_file(str(file_id), self._chat_input_dir(chat_id), preferred_name=preferred_name)

    def _chat_input_dir(self, chat_id: int) -> Path:
        return self.config.telegram_input_root / ("chat_%s" % chat_id)

    def _compose_state_message(
        self,
        state: TelegramConversationState,
        enqueued_job: bool,
    ) -> str:
        if enqueued_job:
            queued_count = len(state.queued_jobs)
            if state.processing:
                return "Da nhan du 1 job moi va them vao hang doi. Hien co %s job dang cho." % queued_count
            if self.input_mode == "simple":
                return "Da nhan du thong tin job: anh san pham va link video. Bot se xu ly job nay ngay bay gio."
            return "Da nhan du thong tin job: anh san pham, link video, link san pham va thoi gian. Bot se xu ly job nay ngay bay gio."
        missing_parts = self._missing_input_parts(state)
        details = []
        if state.draft_errors:
            details.extend(state.draft_errors)
            state.draft_errors = []
        if missing_parts:
            details.append("Bot dang cho them: %s." % ", ".join(missing_parts))
        if details:
            return "\n".join(details)
        return self._instruction_text()
        if enqueued_job:
            queued_count = len(state.queued_jobs)
            if state.processing:
                return "Đã nhận đủ 1 job mới và thêm vào hàng đợi. Hiện có %s job đang chờ." % queued_count
            return "Đã nhận đủ link video TikTok và ảnh sản phẩm. Bot sẽ xử lý job này ngay bây giờ."
        if got_product_id:
            missing_parts = []
            if not state.draft_source_video_url:
                missing_parts.append("link video")
            if state.draft_product_image_path is None:
                missing_parts.append("anh san pham")
            if missing_parts:
                return "Da nhan input. Bot dang cho them: %s." % ", ".join(missing_parts)
        if got_video_url:
            return "Đã nhận link video TikTok. Hãy gửi thêm ảnh sản phẩm để hoàn tất job."
        if got_product_id:
            return "Da nhan link san pham TikTok Shop va lay duoc Product ID. Hay gui them link video va anh san pham."
        if got_image:
            return "Đã nhận ảnh sản phẩm. Hãy gửi thêm link video TikTok để hoàn tất job."
        if state.draft_source_video_url or state.draft_product_id or state.draft_product_image_path is not None:
            missing_parts = []
            if not state.draft_source_video_url:
                missing_parts.append("link video")
            if state.draft_product_image_path is None:
                missing_parts.append("anh san pham")
            if missing_parts:
                return "Bot dang cho them: %s." % ", ".join(missing_parts)
        if state.draft_source_video_url and state.draft_product_image_path is None:
            return "Bot đang chờ ảnh sản phẩm cho job hiện tại."
        if state.draft_product_image_path is not None and not state.draft_source_video_url:
            return "Bot đang chờ link video TikTok cho job hiện tại."
        return self._instruction_text()

    def _enqueue_ready_job(self, chat_id: int, state: TelegramConversationState) -> bool:
        if self._missing_input_parts(state) or state.draft_errors:
            return False
        queued_job = self._prepare_profile_queued_job(
            chat_id,
            TelegramQueuedJob(
                option=int(state.selected_option or 0),
                source_video_url=state.draft_source_video_url,
                product_url=state.draft_product_url or "",
                product_id=state.draft_product_id or "",
                product_image_path=state.draft_product_image_path,
                publish_mode=state.draft_publish_mode or "now",
                scheduled_at=state.draft_scheduled_at or "",
            ),
        )
        state.queued_jobs.append(queued_job)
        state.draft_source_video_url = None
        state.draft_product_url = None
        state.draft_product_id = None
        state.draft_product_image_path = None
        state.draft_publish_mode = None
        state.draft_scheduled_at = ""
        state.workflow_state = STATE_SELECT_OPTION
        state.selected_option = None
        return True

    def _missing_input_parts(self, state: TelegramConversationState) -> List[str]:
        missing_parts = []
        if state.selected_option not in TELEGRAM_OPTION_SPECS:
            missing_parts.append("option")
        if not state.draft_product_image_path:
            missing_parts.append("anh san pham")
        if not state.draft_source_video_url:
            missing_parts.append("link video")
        if self._state_requires_product(state):
            if not state.draft_product_url:
                missing_parts.append("link san pham")
        if self._state_requires_schedule(state):
            if not state.draft_publish_mode:
                missing_parts.append("thoi gian dang")
        return missing_parts

    def _normalize_input_mode(self, value: str) -> str:
        return "full" if (value or "").strip().lower() == "full" else "simple"

    def _parse_schedule_text(self, text_value: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        text = (text_value or "").strip()
        if not text:
            return None, None, None
        normalized = text.lower()
        if normalized in {"now", "dang ngay", "đăng ngay"}:
            return "now", "", None
        for line in text.splitlines():
            if line.strip().lower() in {"now", "dang ngay", "đăng ngay"}:
                return "now", "", None
        full_match = re.search(r"(?:schedule|time|gio|giờ)?\s*:?\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", text, flags=re.IGNORECASE)
        if full_match:
            year, month, day, hour, minute = full_match.groups()
            return self._normalize_schedule_parts(int(year), int(month), int(day), int(hour), int(minute))
        time_match = re.fullmatch(r"(?:schedule|time|gio|giờ)?\s*:?\s*(\d{1,2}):(\d{2})", text, flags=re.IGNORECASE)
        if time_match:
            hour, minute = time_match.groups()
            today = datetime.now()
            return self._normalize_schedule_parts(today.year, today.month, today.day, int(hour), int(minute))
        for line in text.splitlines():
            time_match = re.fullmatch(r"(?:schedule|time|gio|giờ)?\s*:?\s*(\d{1,2}):(\d{2})", line.strip(), flags=re.IGNORECASE)
            if time_match:
                hour, minute = time_match.groups()
                today = datetime.now()
                return self._normalize_schedule_parts(today.year, today.month, today.day, int(hour), int(minute))
        return None, None, None

    def _normalize_schedule_parts(self, year: int, month: int, day: int, hour: int, minute: int) -> tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            value = datetime(year, month, day, hour, minute)
        except ValueError:
            return None, None, "Thoi gian khong hop le. Gui theo mau HH:MM hoac YYYY-MM-DD HH:MM."
        if minute % 5 != 0:
            return None, None, "Phut phai la moc 00, 05, 10, ..., 55."
        if (value - datetime.now()).total_seconds() < 30 * 60:
            return None, None, "Thoi gian dang phai cach hien tai it nhat 30 phut."
        return "scheduled", value.isoformat(sep=" ", timespec="minutes"), None

    def _classify_tiktok_urls(self, text_value: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        source_video_url = None
        product_url = None
        product_id = None
        for match in TIKTOK_URL_RE.findall(text_value or ""):
            clean_url = match.strip().rstrip(".,)]}")
            parsed = urlparse(clean_url)
            hostname = (parsed.hostname or "").lower()
            if not hostname.endswith("tiktok.com"):
                continue
            is_short_url = _is_short_tiktok_url(clean_url)
            resolved_url = self._resolve_tiktok_url(clean_url) if is_short_url else clean_url
            resolved_product_id = extract_tiktok_product_id(resolved_url)
            if is_short_url and not resolved_product_id and source_video_url is not None:
                resolved_url = self._resolve_tiktok_url(
                    clean_url,
                    attempts=TELEGRAM_PRODUCT_URL_RESOLVE_ATTEMPTS,
                    require_product_id=True,
                )
                resolved_product_id = extract_tiktok_product_id(resolved_url)
            if resolved_product_id:
                product_url = resolved_url
                product_id = resolved_product_id
                continue
            if source_video_url is None:
                source_video_url = resolved_url
        return source_video_url, product_url, product_id

    def _resolve_tiktok_url(self, url: str, attempts: int = 1, require_product_id: bool = False) -> str:
        clean_url = (url or "").strip()
        if not clean_url:
            return ""
        max_attempts = max(1, int(attempts or 1))
        last_url = clean_url
        last_error = None
        for attempt in range(1, max_attempts + 1):
            request = Request(clean_url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urlopen(request, timeout=TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS) as response:
                    resolved_url = (response.geturl() or clean_url).strip()
                if resolved_url:
                    last_url = resolved_url
                if not require_product_id or extract_tiktok_product_id(last_url):
                    return last_url
                self.logger.info(
                    "TikTok product URL resolve attempt %s/%s did not expose product ID: %s",
                    attempt,
                    max_attempts,
                    last_url,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    self.logger.warning("Could not resolve TikTok URL %s: %s", clean_url, exc)
                else:
                    self.logger.info(
                        "TikTok URL resolve attempt %s/%s failed for %s: %s",
                        attempt,
                        max_attempts,
                        clean_url,
                        exc,
                    )
        if require_product_id and not extract_tiktok_product_id(last_url):
            detail = " Last error: %s" % last_error if last_error else ""
            self.logger.warning("Could not resolve TikTok product ID after %s attempts: %s.%s", max_attempts, clean_url, detail)
        return last_url

    def _instruction_text(self) -> str:
        return (
            "Gửi ảnh sản phẩm kèm caption chứa link:\n\n"
            "https://vt.tiktok.com/abc123\n"
            "https://shopee.vn/product/xyz\n"
            "22:30\n\n"
            "Link đầu là video, link thứ hai là sản phẩm, giờ đăng có thể bỏ qua. "
            "Lệnh /myid trả về Chat ID, /reset xoá input tạm, /cleanup dọn input/output."
        )
        if self.input_mode == "simple":
            return (
                "Che do nhanh: gui moi job gom 1 anh san pham va 1 link video TikTok. "
                "Bot se gom input trong khoang 10 giay roi moi phan hoi. "
                "Link san pham va thoi gian co the bo qua; video se vao hang doi voi che do now. "
                "Lenh /myid tra ve Chat ID. /reset xoa input tam cua chat hien tai. "
                "/cleanup xoa video/anh input-output va lam moi hang doi Telegram."
            )
        return (
            "Gui moi job gom: 1 anh san pham, 1 link video TikTok, 1 link san pham TikTok Shop, "
            "va thoi gian dang. Bot se gom input trong khoang 10 giay roi moi phan hoi. "
            "Thoi gian co the la HH:MM cho hom nay, YYYY-MM-DD HH:MM cho ngay cu the, hoac now. "
            "Lenh /myid tra ve Chat ID. /reset xoa input tam cua chat hien tai. "
            "/cleanup xoa video/anh input-output va lam moi hang doi Telegram."
        )
        return (
            "Hãy gửi cho bot mỗi job gồm 2 món: 1 link video TikTok public và 1 ảnh sản phẩm. "
            "Bạn có thể gửi link trước rồi gửi ảnh sau, hoặc gửi ảnh kèm caption chứa link. "
            "Lệnh /myid sẽ trả về Chat ID của bạn. "
            "Lệnh /reset sẽ xoá input tạm và hàng đợi của chat hiện tại. "
            "Lệnh /cleanup sẽ xoa toan bo video, anh trong input/output va lam moi hang doi Telegram."
        )

    def _chat_identity_text(self, message: Dict[str, object]) -> str:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = str(chat.get("type") or "private")
        username = str(chat.get("username") or "").strip()
        title = str(chat.get("title") or "").strip()
        lines = ["Chat ID: %s" % chat_id, "Loại chat: %s" % chat_type]
        if username:
            lines.append("Username: @%s" % username)
        if title:
            lines.append("Tên chat: %s" % title)
        return "\n".join(lines)

    def latest_chat_id(self) -> Optional[int]:
        with self._state_lock:
            return self._recent_chat_ids[-1] if self._recent_chat_ids else None

    def has_processing_jobs(self) -> bool:
        with self._state_lock:
            return any(state.processing for state in self._chat_states.values())

    def clear_pending_jobs(self) -> None:
        with self._state_lock:
            for state in self._chat_states.values():
                if state.processing:
                    continue
                state.draft_source_video_url = None
                state.draft_product_url = None
                state.draft_product_id = None
                state.draft_product_image_path = None
                state.draft_publish_mode = None
                state.draft_scheduled_at = ""
                state.draft_errors = []
                state.workflow_state = STATE_SELECT_OPTION
                state.selected_option = None
                if state.pending_timer is not None:
                    state.pending_timer.cancel()
                    state.pending_timer = None
                state.queued_jobs = []

    def _maybe_cleanup_expired_media(self) -> None:
        if not self.config.telegram_auto_cleanup_enabled:
            return
        now = time.time()
        interval_seconds = max(60, int(self.config.telegram_cleanup_interval_seconds))
        if self._last_auto_cleanup_at and now - self._last_auto_cleanup_at < interval_seconds:
            return
        if self.has_processing_jobs():
            return
        self._last_auto_cleanup_at = now
        max_age_seconds = max(300, int(self.config.telegram_cleanup_max_age_seconds))
        report = cleanup_media_storage(self.config, older_than_seconds=max_age_seconds)
        if report.deleted_files or report.errors:
            self.logger.info("Telegram auto cleanup: %s", format_cleanup_report(report))

    def _remember_chat_id(self, chat_id: int) -> None:
        with self._state_lock:
            if chat_id in self._recent_chat_ids:
                self._recent_chat_ids.remove(chat_id)
            self._recent_chat_ids.append(chat_id)
            if len(self._recent_chat_ids) > 10:
                self._recent_chat_ids = self._recent_chat_ids[-10:]

    def _is_chat_allowed(self, chat_id: int) -> bool:
        allowed_chat_ids = tuple(self.config.telegram_allowed_chat_ids or ())
        if not allowed_chat_ids:
            return True
        return int(chat_id) in allowed_chat_ids
