"""Telegram polling bot that feeds queued single-item jobs into the editor pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import logging
import mimetypes
from pathlib import Path
import re
import shutil
import socket
import threading
import time
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

from auto_tiktok_editor.app.media_cleanup import cleanup_media_storage, format_cleanup_report
from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.runtime import ensure_local_telegram_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec


TIKTOK_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TELEGRAM_API_RETRY_ATTEMPTS = 3
TELEGRAM_API_RETRY_BASE_DELAY_SECONDS = 2.0
TELEGRAM_JOB_RETRY_ATTEMPTS = 3
TIKTOK_DOWNLOAD_RETRY_MARKERS = (
    "download",
    "tiktok",
    "lazy-down",
    "yt-dlp",
    "source video",
    "playable video stream",
    "non-video artifacts",
)


@dataclass
class TelegramQueuedJob:
    source_video_url: str
    product_image_path: Path


@dataclass
class TelegramConversationState:
    draft_source_video_url: Optional[str] = None
    draft_product_image_path: Optional[Path] = None
    queued_jobs: List[TelegramQueuedJob] = field(default_factory=list)
    processing: bool = False


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

    def send_message(self, chat_id: int, text: str) -> None:
        self._call_json_api(
            "sendMessage",
            payload={
                "chat_id": str(chat_id),
                "text": text,
            },
            timeout=30,
        )

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
        self.orchestrator = orchestrator or SessionOrchestrator(
            config=self.config,
            license_checkpoint=license_checkpoint,
            logger=self.logger,
        )

    def run(self, chat_id: int, source_video_url: str, product_image_path: Path) -> TelegramJobResult:
        session_spec = SessionSpec(
            items=[
                SessionItemSpec(
                    row_id="chat_%s" % chat_id,
                    source_video_url=source_video_url,
                    product_image=Path(product_image_path),
                )
            ],
            output_root_dir=self.config.default_output_root,
            session_name="telegram_%s" % chat_id,
            cookies_file=None,
        )
        session_result = self.orchestrator.run(session_spec)
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
        self._chat_states = {}  # type: Dict[int, TelegramConversationState]
        self._recent_chat_ids: List[int] = []
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_auto_cleanup_at = 0.0
        self._poll_offset_path = Path(self.config.telegram_input_root) / "_telegram_poll_offset.txt"

    def serve_forever(self) -> None:
        offset = self._load_poll_offset()
        self.logger.info("Telegram bot worker started.")
        while not self._stop_event.is_set():
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
        if text_value.startswith("/start"):
            self.client.send_message(chat_id, self._instruction_text())
            return
        if text_value.startswith("/myid"):
            self.client.send_message(chat_id, self._chat_identity_text(message))
            return
        if text_value.startswith("/reset"):
            with self._state_lock:
                self._chat_states.pop(chat_id, None)
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

        received_url = self._extract_tiktok_url(text_value)
        try:
            image_path = self._download_image_from_message(chat_id, message)
        except Exception as exc:
            self.logger.warning("Could not download Telegram image for chat %s: %s", chat_id, exc)
            self.client.send_message(
                chat_id,
                "Khong tai duoc anh tu Telegram sau khi thu lai. Hay gui lai anh. Loi: %s" % exc,
            )
            return

        with self._state_lock:
            state = self._chat_states.get(chat_id)
            if state is None:
                state = TelegramConversationState()
                self._chat_states[chat_id] = state
            if received_url:
                state.draft_source_video_url = received_url
            if image_path is not None:
                state.draft_product_image_path = image_path
            enqueued_job = self._enqueue_ready_job(state)
            response_text = self._compose_state_message(
                state=state,
                got_url=received_url is not None,
                got_image=image_path is not None,
                enqueued_job=enqueued_job,
            )

        if response_text:
            self.client.send_message(chat_id, response_text)
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
            queued_job.product_image_path,
        )

    def _run_job_and_reply(self, chat_id: int, source_video_url: str, product_image_path: Path) -> None:
        result = None
        try:
            result = self._run_job_with_processing_retries(chat_id, source_video_url, product_image_path)
            if not result.ok or result.final_video_path is None or not result.final_video_path.exists():
                self.client.send_message(
                    chat_id,
                    "Job bi loi: %s" % (result.error or "khong tim thay video dau ra."),
                )
                return
            caption = result.source_title or "Video đã edit xong."
            self.client.send_document(
                chat_id,
                result.final_video_path,
                caption=caption,
                filename="video_final.mp4",
            )
        except Exception as exc:
            self.logger.exception("Telegram job failed for chat %s.", chat_id)
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

    def _run_job_with_processing_retries(
        self,
        chat_id: int,
        source_video_url: str,
        product_image_path: Path,
    ) -> TelegramJobResult:
        last_result = None
        for attempt in range(1, TELEGRAM_JOB_RETRY_ATTEMPTS + 1):
            try:
                result = self.job_runner.run(chat_id, source_video_url, product_image_path)
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
        paths.append(Path(product_image_path))
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
        got_url: bool,
        got_image: bool,
        enqueued_job: bool,
    ) -> str:
        if enqueued_job:
            queued_count = len(state.queued_jobs)
            if state.processing:
                return "Đã nhận đủ 1 job mới và thêm vào hàng đợi. Hiện có %s job đang chờ." % queued_count
            return "Đã nhận đủ link TikTok và ảnh sản phẩm. Bot sẽ xử lý job này ngay bây giờ."
        if got_url:
            return "Đã nhận link TikTok. Hãy gửi thêm ảnh sản phẩm để hoàn tất job."
        if got_image:
            return "Đã nhận ảnh sản phẩm. Hãy gửi thêm link TikTok để hoàn tất job."
        if state.draft_source_video_url and state.draft_product_image_path is None:
            return "Bot đang chờ ảnh sản phẩm cho job hiện tại."
        if state.draft_product_image_path is not None and not state.draft_source_video_url:
            return "Bot đang chờ link TikTok cho job hiện tại."
        return self._instruction_text()

    def _enqueue_ready_job(self, state: TelegramConversationState) -> bool:
        if not state.draft_source_video_url or state.draft_product_image_path is None:
            return False
        state.queued_jobs.append(
            TelegramQueuedJob(
                source_video_url=state.draft_source_video_url,
                product_image_path=state.draft_product_image_path,
            )
        )
        state.draft_source_video_url = None
        state.draft_product_image_path = None
        return True

    def _extract_tiktok_url(self, text_value: str) -> Optional[str]:
        for match in TIKTOK_URL_RE.findall(text_value or ""):
            parsed = urlparse(match.strip())
            hostname = (parsed.hostname or "").lower()
            if hostname.endswith("tiktok.com"):
                return match.strip()
        return None

    def _instruction_text(self) -> str:
        return (
            "Hãy gửi cho bot từng cặp gồm 1 link TikTok public và 1 ảnh sản phẩm. "
            "Mỗi khi đủ 1 cặp, bot sẽ tự đưa vào hàng đợi và xử lý lần lượt. "
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
                state.draft_product_image_path = None
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
