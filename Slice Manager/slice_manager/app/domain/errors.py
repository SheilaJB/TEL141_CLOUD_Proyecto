class DomainError(Exception):
    status_code = 500


class AccessDeniedError(DomainError):
    status_code = 403


class ConflictError(DomainError):
    status_code = 409


class InsufficientCapacityError(DomainError):
    status_code = 409


class ResourceNotFoundError(DomainError):
    status_code = 404


class WorkflowUnavailableError(DomainError):
    status_code = 503


class WorkflowStartError(Exception):
    pass
