"""Personal Telegram worker entry point.

This entry point is intentionally separate so Render and local bot runs only
start the personal Telegram pipeline.
"""

from __future__ import annotations

import os
import sys

from auto_tiktok_editor.cli import main as cli_main


def main() -> int:
    os.environ.setdefault("AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM", "1")
    return cli_main(["telegram-bot"])


if __name__ == "__main__":
    sys.exit(main())
