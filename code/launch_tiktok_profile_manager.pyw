"""GUI launcher used by the taskbar shortcut."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
LOCAL_TEMP = PROJECT_ROOT / "tmp"

os.chdir(PROJECT_ROOT)
LOCAL_TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(LOCAL_TEMP)
os.environ["TMP"] = str(LOCAL_TEMP)
sys.path.insert(0, str(SOURCE_ROOT))

from auto_tiktok_editor.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["profile-manager"]))
