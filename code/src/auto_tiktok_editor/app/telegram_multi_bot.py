"""Run multiple Telegram bot pollers in one process with a shared job pool."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path
import re
from threading import Thread
import time
from typing import Iterable, List, Optional, Tuple

from auto_tiktok_editor.app.fashion_bot import FashionProductBotService
from auto_tiktok_editor.app.telegram_bot import TelegramBotService
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager


@dataclass
class TelegramBotRuntimeSpec:
    name: str
    bot_token: str
    chat_ids: Tuple[int, ...] = ()
    bot_type: str = "video"

    @property
    def chat_id(self) -> Optional[int]:
        return self.chat_ids[0] if self.chat_ids else None


def load_telegram_bot_specs(path: Path) -> List[TelegramBotRuntimeSpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_bots = payload.get("bots") if isinstance(payload, dict) else payload
    if not isinstance(raw_bots, list):
        raise ValueError("File cau hinh multi bot phai co key 'bots' dang list.")
    specs_by_token = {}
    chat_ids_by_token = {}
    for index, item in enumerate(raw_bots, start=1):
        if not isinstance(item, dict):
            raise ValueError("Bot #%s khong phai object hop le." % index)
        token = str(item.get("bot_token") or item.get("token") or "").strip()
        if not token:
            raise ValueError("Bot #%s thieu bot_token." % index)
        chat_ids = _normalize_chat_ids(item)
        name = str(item.get("name") or ("bot_%03d" % index)).strip() or ("bot_%03d" % index)
        bot_type = _normalize_bot_type(item.get("type") or item.get("mode") or "video")
        if token not in specs_by_token:
            specs_by_token[token] = TelegramBotRuntimeSpec(name=name, bot_token=token, bot_type=bot_type)
            chat_ids_by_token[token] = set()
        elif specs_by_token[token].bot_type != bot_type:
            raise ValueError("Một bot token không thể vừa là video bot vừa là Fashion bot.")
        chat_ids_by_token[token].update(chat_ids)
    specs = [
        TelegramBotRuntimeSpec(
            name=spec.name,
            bot_token=spec.bot_token,
            chat_ids=tuple(sorted(chat_ids_by_token.get(spec.bot_token) or ())),
            bot_type=spec.bot_type,
        )
        for spec in specs_by_token.values()
    ]
    if not specs:
        raise ValueError("File cau hinh multi bot chua co bot nao hop le.")
    return specs


def run_multi_telegram_bots(
    specs: Iterable[TelegramBotRuntimeSpec],
    base_config: Optional[PipelineConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> int:
    config = base_config or PipelineConfig.from_env()
    logger = logger or logging.getLogger("auto_tiktok_editor.telegram.multi")
    specs = list(specs)
    max_workers = max(1, int(config.max_parallel_session_items))
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="telegram-job")
    profile_manager = _load_profile_manager(logger)
    services = []
    threads = []
    try:
        for index, spec in enumerate(specs, start=1):
            bot_config = _config_for_bot(config, spec, index, profile_manager=profile_manager)
            if spec.bot_type == "fashion":
                service = FashionProductBotService(
                    config=bot_config,
                    bot_token=spec.bot_token,
                    allowed_chat_ids=spec.chat_ids,
                    manager=profile_manager,
                    logger=logger,
                    executor=executor,
                )
            else:
                service = TelegramBotService(config=bot_config, logger=logger, executor=executor)
            thread = _BotThread(service=service, name="telegram-bot-%s" % _safe_name(spec.name))
            services.append(service)
            threads.append(thread)
            thread.start()
            logger.info(
                "Started %s Telegram bot '%s' mapped to profile '%s' with cut mode '%s'.",
                spec.bot_type,
                spec.name,
                bot_config.tiktok_profile_slug,
                bot_config.video_cut_mode,
            )
        print("Dang chay %s Telegram bot. Tong so job xu ly cung luc toi da: %s." % (len(threads), max_workers))
        while all(thread.is_alive() for thread in threads):
            time.sleep(1.0)
        failed = [thread for thread in threads if thread.error is not None]
        if failed:
            raise failed[0].error
        return 0
    except KeyboardInterrupt:
        print("Dang dung cac Telegram bot...")
        return 0
    finally:
        for service in services:
            service.stop()
        for thread in threads:
            thread.join(timeout=2.0)
        executor.shutdown(wait=False)


def _config_for_bot(
    base_config: PipelineConfig,
    spec: TelegramBotRuntimeSpec,
    index: int,
    profile_manager: TikTokProfileManager | None = None,
) -> PipelineConfig:
    allowed_chat_ids = tuple(spec.chat_ids or ())
    profile_slug = _safe_name(spec.name or ("bot_%03d" % index))
    input_root = Path(base_config.telegram_input_root) / profile_slug
    profile_cut_mode = _profile_cut_mode_for_bot_name(profile_slug, profile_manager)
    return replace(
        base_config,
        allow_local_telegram=True,
        telegram_bot_token=spec.bot_token,
        telegram_delivery_chat_id=str(spec.chat_id or ""),
        telegram_allowed_chat_ids=allowed_chat_ids,
        telegram_input_root=input_root,
        tiktok_profile_slug=profile_slug,
        video_cut_mode=profile_cut_mode or base_config.video_cut_mode,
    )


def _load_profile_manager(logger: logging.Logger) -> TikTokProfileManager | None:
    try:
        return TikTokProfileManager()
    except Exception as exc:
        logger.warning("Could not load TikTok Profile Manager for Telegram bot mapping: %s", exc)
        return None


def _profile_cut_mode_for_bot_name(profile_slug: str, profile_manager: TikTokProfileManager | None) -> str:
    if profile_manager is None:
        return ""
    account = profile_manager.find_account_for_profile_slug(profile_slug)
    if account is None:
        return ""
    return str(getattr(account, "cut_mode", "") or "").strip().lower()


def _normalize_chat_id(value) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("chat_id phai la so nguyen hop le.")


def _normalize_chat_ids(item) -> Tuple[int, ...]:
    values = []
    if "chat_ids" in item and item.get("chat_ids") is not None:
        raw_values = item.get("chat_ids")
        if not isinstance(raw_values, list):
            raise ValueError("chat_ids phai la list so nguyen hop le.")
        values.extend(raw_values)
    else:
        values.append(item.get("chat_id") or item.get("delivery_chat_id"))
    chat_ids = []
    for value in values:
        chat_id = _normalize_chat_id(value)
        if chat_id is not None:
            chat_ids.append(chat_id)
    return tuple(chat_ids)


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "bot"


def _normalize_bot_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"video", "fashion"}:
        return normalized
    raise ValueError("type của bot phải là 'video' hoặc 'fashion'.")


class _BotThread(Thread):
    def __init__(self, service, name: str):
        super(_BotThread, self).__init__(target=self._run, name=name, daemon=True)
        self.service = service
        self.error = None

    def _run(self) -> None:
        try:
            self.service.serve_forever()
        except Exception as exc:
            self.error = exc
            raise
