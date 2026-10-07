from enum import StrEnum

from contracts.base import ContractModel


class ErrorCode(StrEnum):
    INVALID_SPEC = "InvalidSpec"
    CAPACITY_EXCEEDED = "CapacityExceeded"
    RESOURCE_NOT_FOUND = "ResourceNotFound"
    CONFLICT = "Conflict"
    BACKEND_UNAVAILABLE = "BackendUnavailable"


class ErrorResponse(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool
