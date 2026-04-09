"""Wrapper around subprocess execution for external media tools."""

from __future__ import annotations

from pathlib import Path
import logging
import os
import shutil
import subprocess

from auto_tiktok_editor.exceptions import ExternalToolError


class CommandRunner(object):
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)

    def ensure_tool(self, tool_name):
        if not tool_name:
            raise ExternalToolError("Missing tool name.")
        potential_path = Path(tool_name)
        if potential_path.exists():
            return
        if shutil.which(tool_name) is not None:
            return
        raise ExternalToolError(
            "Required external tool '%s' was not found in PATH or as a file path." % tool_name
        )

    def run(self, args, cwd=None, check=True, capture_output=True):
        if not args:
            raise ExternalToolError("No command specified.")
        self.ensure_tool(args[0])
        self.logger.debug("Running command: %s", " ".join(args))
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and completed.returncode != 0:
            raise ExternalToolError(
                "Command failed (%s): %s" % (completed.returncode, completed.stderr.strip())
            )
        return completed

    @property
    def devnull(self):
        return os.devnull
