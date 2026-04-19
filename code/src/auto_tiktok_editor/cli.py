"""CLI entry point for the session-based Auto TikTok Editor MVP."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from auto_tiktok_editor.app.orchestrator import SessionOrchestrator
from auto_tiktok_editor.commercial_runtime import ensure_local_telegram_allowed, ensure_runtime_allowed
from auto_tiktok_editor.license.exceptions import LicenseAuthenticationRequired, LicenseError
from auto_tiktok_editor.license.guard import LicenseGuard
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SessionItemSpec, SessionSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto TikTok Editor MVP")
    parser.add_argument("--log-level", default="INFO", help="Python log level")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ui", help="Launch the local desktop UI")
    subparsers.add_parser("telegram-bot", help="Run the Telegram bot worker")

    run_parser = subparsers.add_parser("run-session", help="Run a session from a JSON manifest")
    run_parser.add_argument("--session-file", required=True, help="Path to a JSON session manifest")
    return parser


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_session_spec(manifest_path: Path, config: PipelineConfig) -> SessionSpec:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_root = payload.get("output_root_dir") or str(config.default_output_root)
    cookies_file = payload.get("cookies_file")
    items = []
    for index, item in enumerate(payload.get("items") or []):
        product_image = item.get("product_image")
        items.append(
            SessionItemSpec(
                row_id=item.get("row_id") or "row_%03d" % (index + 1),
                source_video_url=item.get("source_video_url", ""),
                product_image=Path(product_image) if product_image else None,
                output_basename=item.get("output_basename"),
                shuffle_seed=item.get("shuffle_seed"),
            )
        )
    return SessionSpec(
        items=items,
        output_root_dir=Path(output_root),
        session_name=payload.get("session_name"),
        cookies_file=Path(cookies_file) if cookies_file else None,
    )


def run_headless_session(manifest_path: Path) -> int:
    config = PipelineConfig.from_env()
    _ensure_runtime_policy(config, "run-session")
    guard = LicenseGuard()
    if _require_cached_license_session(guard, "run-session") is None:
        return 1
    orchestrator = SessionOrchestrator(config=config, license_checkpoint=guard.heartbeat)
    result = orchestrator.run(load_session_spec(manifest_path, config))
    print(json.dumps(result.summary, indent=2, ensure_ascii=False))
    return 0 if result.status in ("completed_with_success", "completed_with_partial_failure") else 1


def _require_cached_license_session(guard: LicenseGuard, command_name: str):
    try:
        return guard.ensure_valid_session()
    except LicenseAuthenticationRequired:
        print(
            "Lenh '%s' can phien dang nhap hop le. "
            "Hay mo giao dien tool va dang nhap bang tai khoan admin cap truoc."
            % command_name
        )
        return None
    except LicenseError as exc:
        print("Khong the xac thuc license cho '%s': %s" % (command_name, exc))
        return None


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    config = PipelineConfig.from_env()
    if args.command in (None, "ui"):
        from auto_tiktok_editor.ui.app import launch_ui

        _ensure_runtime_policy(config, "ui")
        return launch_ui(config=config)
    if args.command == "run-session":
        return run_headless_session(Path(args.session_file))
    if args.command == "telegram-bot":
        from auto_tiktok_editor.app.telegram_bot import TelegramBotService

        _ensure_runtime_policy(config, "telegram-bot")
        _ensure_local_telegram_policy(config, "telegram-bot")
        guard = LicenseGuard()
        if _require_cached_license_session(guard, "telegram-bot") is None:
            return 1
        service = TelegramBotService(config=config, runtime_checkpoint=guard.heartbeat)
        service.serve_forever()
        return 0
    parser.error("Unknown command.")
    return 2


def _ensure_runtime_policy(config: PipelineConfig, surface: str) -> None:
    try:
        ensure_runtime_allowed(config, surface=surface)
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)


def _ensure_local_telegram_policy(config: PipelineConfig, surface: str) -> None:
    try:
        ensure_local_telegram_allowed(config, surface=surface)
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
