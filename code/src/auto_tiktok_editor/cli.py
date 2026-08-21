"""CLI entry points required by the TikTok Profile Manager."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.runtime import ensure_local_telegram_allowed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TikTok Profile Manager")
    parser.add_argument("--log-level", default="INFO", help="Python log level")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("profile-manager", help="Launch the TikTok Profile Manager UI")
    subparsers.add_parser("telegram-bot", help="Run the Telegram bot worker")
    multi_bot_parser = subparsers.add_parser("telegram-bots", help="Run multiple Telegram bot workers")
    multi_bot_parser.add_argument(
        "--bots-file",
        default="telegram_bots.json",
        help="Path to JSON file containing Telegram bot token/chat mappings",
    )
    return parser


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    config = PipelineConfig.from_env()

    if args.command in (None, "profile-manager"):
        from auto_tiktok_editor.tiktok_profiles.qt_ui.app import launch_app

        return launch_app(config=config)
    if args.command == "telegram-bot":
        from auto_tiktok_editor.app.telegram_bot import TelegramBotService

        _ensure_local_telegram_policy(config, "telegram-bot")
        service = TelegramBotService(config=config)
        service.serve_forever()
        return 0
    if args.command == "telegram-bots":
        from auto_tiktok_editor.app.telegram_multi_bot import load_telegram_bot_specs, run_multi_telegram_bots

        specs = load_telegram_bot_specs(Path(args.bots_file))
        return run_multi_telegram_bots(specs, base_config=config)
    parser.error("Unknown command.")
    return 2


def _ensure_local_telegram_policy(config: PipelineConfig, surface: str) -> None:
    try:
        ensure_local_telegram_allowed(config, surface=surface)
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
