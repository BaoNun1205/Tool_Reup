"""Pipeline and session state tracking with text log generation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import List, Optional


@dataclass
class LogEvent:
    timestamp: str
    level: str
    stage: str
    message: str


class PipelineRecorder(object):
    def __init__(self, run_id: str, run_label: str = "job", logger: Optional[logging.Logger] = None):
        self.run_id = run_id
        self.run_label = run_label
        self.logger = logger or logging.getLogger(__name__)
        self.current_stage = "idle"
        self.events = []  # type: List[LogEvent]
        self.warnings = []  # type: List[str]

    def transition(self, stage: str) -> None:
        self.current_stage = stage
        self.info("Entered stage '%s'." % stage)

    def info(self, message: str) -> None:
        self._record("INFO", message)
        self.logger.info(message)

    def warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
        self._record("WARNING", message)
        self.logger.warning(message)

    def error(self, message: str) -> None:
        self._record("ERROR", message)
        self.logger.error(message)

    def extend_warnings(self, warnings: List[str]) -> None:
        for warning in warnings:
            self.warning(warning)

    def to_log_text(self) -> str:
        lines = ["%s_id=%s" % (self.run_label, self.run_id)]
        for event in self.events:
            lines.append("[%s] %s %s %s" % (event.timestamp, event.level, event.stage, event.message))
        return "\n".join(lines) + "\n"

    def _record(self, level: str, message: str) -> None:
        self.events.append(
            LogEvent(
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                level=level,
                stage=self.current_stage,
                message=message,
            )
        )
