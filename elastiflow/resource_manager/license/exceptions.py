class LicenseError(Exception):
    """Base class for license manager errors."""

class UnknownPool(LicenseError):
    """Requested pool doesn't exist."""

class InsufficientTokens(LicenseError):
    """Request exceeds available tokens."""

class InvalidHold(LicenseError):
    """Referenced hold doesn't exist."""

class HoldExpired(LicenseError):
    """Hold TTL elapsed before commit."""

class AlreadyCommitted(LicenseError):
    """Committing same hold twice."""
