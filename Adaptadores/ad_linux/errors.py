"""Errors returned by Linux adapter handlers."""


class RetryableError(Exception):
    """A host operation may succeed when Temporal retries it."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)

class NonRetryableError(Exception):
    """An action cannot succeed without changing its input or environment."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)
