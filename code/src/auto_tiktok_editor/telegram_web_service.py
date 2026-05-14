"""Render Web Service wrapper for the personal Telegram bot.

Render free services need an HTTP port. This module exposes a small health
endpoint while the Telegram long-polling bot runs in a background thread.
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from auto_tiktok_editor.app.telegram_bot import TelegramBotService
from auto_tiktok_editor.config import PipelineConfig


class _HealthHandler(BaseHTTPRequestHandler):
    service_state = {
        "bot_started": False,
        "bot_error": "",
    }

    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        status_code = 200 if not self.service_state["bot_error"] else 500
        payload = {
            "ok": status_code == 200,
            "bot_started": self.service_state["bot_started"],
            "bot_error": self.service_state["bot_error"],
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        logging.getLogger("auto_tiktok_editor.telegram.web").info(format, *args)


def _run_bot(config: PipelineConfig) -> None:
    logger = logging.getLogger("auto_tiktok_editor.telegram.web")
    try:
        service = TelegramBotService(config=config, logger=logger)
        _HealthHandler.service_state["bot_started"] = True
        service.serve_forever()
    except Exception as exc:
        logger.exception("Telegram bot service stopped.")
        _HealthHandler.service_state["bot_error"] = str(exc)


def main() -> int:
    os.environ.setdefault("AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM", "1")
    os.environ.setdefault("AUTO_EDITOR_OUTPUT_ROOT", "/tmp/auto-tiktok-output")
    os.environ.setdefault("AUTO_EDITOR_TELEGRAM_CLEANUP_AFTER_JOB", "1")
    os.environ.setdefault("AUTO_EDITOR_MAX_PARALLEL_SESSION_ITEMS", "1")
    logging.basicConfig(
        level=getattr(logging, os.getenv("AUTO_EDITOR_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = PipelineConfig.from_env()
    bot_thread = threading.Thread(target=_run_bot, args=(config,), name="telegram-bot", daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    logging.getLogger("auto_tiktok_editor.telegram.web").info("Health server listening on port %s.", port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
