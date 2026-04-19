class LicenseError(Exception):
    """Base class for license-related client errors."""


class LicenseAuthenticationRequired(LicenseError):
    """Raised when the client needs an interactive login."""


class LicenseVerificationError(LicenseError):
    """Raised when a token or session payload cannot be verified."""


class LicenseServerUnavailable(LicenseError):
    """Raised when the server cannot be reached and no valid cache exists."""
