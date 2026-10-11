"""Classified errors for Compute Manager operations."""


class RetryableError(Exception):
    """An operation may succeed when Temporal retries it."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class NonRetryableError(Exception):
    """An operation cannot succeed without changing its input or state."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)
