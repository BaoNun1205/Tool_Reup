from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import traceback

from auto_tiktok_editor.commercial_runtime import ensure_runtime_allowed
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.ui.app import launch_ui


def _log_file_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        path = Path(local_app_data) / "AutoTikTokEditor" / "commercial_startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("commercial_startup.log")
    return Path(__file__).resolve().parents[3] / "commercial_startup.log"


def main() -> int:
    os.environ["AUTO_EDITOR_COMMERCIAL_MODE"] = "1"
    os.environ["AUTO_EDITOR_REQUIRE_FROZEN_BUILD"] = "1"
    log_path = _log_file_path()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("BOOT commercial_entry main()\n")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        filename=str(log_path),
        filemode="a",
        force=True,
    )
    try:
        config = PipelineConfig.from_env()
        logging.getLogger("auto_tiktok_editor.commercial").info(
            "Commercial startup. runtime_is_frozen=%s commercial_mode=%s allow_local_telegram=%s",
            config.runtime_is_frozen,
            config.commercial_mode,
            config.allow_local_telegram,
        )
        ensure_runtime_allowed(config, surface="commercial-ui")
        exit_code = launch_ui(config=config)
        logging.getLogger("auto_tiktok_editor.commercial").info("Commercial UI exited with code %s.", exit_code)
        return exit_code
    except Exception:
        logging.getLogger("auto_tiktok_editor.commercial").exception("Commercial startup failed.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
