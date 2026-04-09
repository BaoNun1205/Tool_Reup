"""Project-specific exceptions."""


class EditorError(Exception):
    """Base exception for the editor pipeline."""


class ValidationError(EditorError):
    """Raised when user input is invalid."""


class DownloadError(EditorError):
    """Raised when source media cannot be downloaded."""


class ExternalToolError(EditorError):
    """Raised when a required external tool is unavailable or fails."""


class ProbeError(EditorError):
    """Raised when media metadata cannot be parsed."""


class PipelineStageError(EditorError):
    """Raised when a pipeline stage cannot complete."""


class SessionValidationError(EditorError):
    """Raised when a session contains invalid rows or violates session rules."""

    def __init__(self, message, row_errors=None):
        super(SessionValidationError, self).__init__(message)
        self.row_errors = row_errors or {}
