"""Custom exceptions for the court booking bot."""


class ElementNotFoundError(Exception):
    """Raised when an element cannot be found on the page."""
    pass


class LoginError(Exception):
    """Raised when login fails."""
    pass


class DateSelectionError(Exception):
    """Raised when date selection fails."""
    pass


class CourtUnavailableError(Exception):
    """Raised when a court is not available for booking."""
    pass
