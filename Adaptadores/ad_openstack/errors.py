"""Errors returned by OpenStack adapter handlers."""


class RetryableError(Exception):
    """An OpenStack operation may succeed when Temporal retries it."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class NonRetryableError(Exception):
    """An action cannot succeed without changing its input or environment."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class NotFoundError(NonRetryableError):
    """The OpenStack resource does not exist (HTTP 404).

    Handlers catch it to report `_already_absent` on deletes; uncaught, it is
    a non-retryable failure like any other.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__("OPENSTACK_NOT_FOUND", message)
