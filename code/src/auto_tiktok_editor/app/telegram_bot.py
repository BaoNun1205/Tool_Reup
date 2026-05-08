"""Telegram polling bot that feeds queued single-item jobs into the editor pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import logging
import mimetypes
from pathlib import Path
import re
import threading
import time
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import uuid

from auto_tiktok_editor.app.media_cleanup import cleanup_media_storage, format_cleanup_report
from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.commercial_runtime import ensure_local_telegram_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec


TIKTOK_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


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
        return self._call_json_api("getUpdates", payload=payload, timeout=timeout_seconds + 10)

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
        with urlopen(request, timeout=120) as response:
            local_path.write_bytes(response.read())
        return local_path

    def _call_json_api(self, method: str, payload: Optional[Dict[str, object]] = None, timeout: int = 30):
        encoded = urlencode(payload or {}).encode("utf-8")
        request = Request("%s/%s" % (self.api_root, method), data=encoded)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError("Telegram API request failed for %s: %s" % (method, exc))
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
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError("Telegram upload failed for %s: %s" % (method, exc))
        if not data.get("ok"):
            raise RuntimeError("Telegram upload returned an error for %s: %s" % (method, data))


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
                session_id=session_result.session_id,
            )
        item_result = session_result.items[0]
        if item_result.status != "completed":
            return TelegramJobResult(
                ok=False,
                error=item_result.error or "Pipeline không thể hoàn tất video này.",
                session_id=session_result.session_id,
            )
        return TelegramJobResult(
            ok=True,
            final_video_path=item_result.artifacts.final_video_path,
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

    def serve_forever(self) -> None:
        offset = None
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
                    self.handle_update(update)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.logger.exception("Telegram polling loop failed: %s", exc)
                if self._stop_event.is_set():
                    break
                time.sleep(max(1, int(self.config.telegram_poll_interval_seconds)))

    def stop(self) -> None:
        self._stop_event.set()

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
        image_path = self._download_image_from_message(chat_id, message)

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
        try:
            result = self.job_runner.run(chat_id, source_video_url, product_image_path)
            if not result.ok or result.final_video_path is None or not result.final_video_path.exists():
                self.client.send_message(
                    chat_id,
                    "Job bị lỗi: %s" % (result.error or "không tìm thấy video đầu ra."),
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
            self.client.send_message(chat_id, "Có lỗi khi xử lý job: %s" % exc)
        finally:
            with self._state_lock:
                state = self._chat_states.get(chat_id)
                if state is not None:
                    state.processing = False
                    has_follow_up = bool(state.queued_jobs)
                else:
                    has_follow_up = False
            if has_follow_up:
                self._maybe_start_job(chat_id)

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
