"""Storage-layer domain errors mapped by API later."""


class StorageError(Exception):
    code: str = "STORAGE_ERROR"


class VersionConflictError(StorageError):
    code = "VERSION_CONFLICT"


class IdempotencyConflictError(StorageError):
    code = "IDEMPOTENCY_CONFLICT"


class IdempotencyInProgressError(StorageError):
    code = "INVALID_STATE"


class LeaseConflictError(StorageError):
    code = "INVALID_STATE"


class LeaseExpiredError(StorageError):
    code = "INVALID_STATE"


class AttemptSlotBusyError(StorageError):
    code = "INVALID_STATE"


class OutboxClaimError(StorageError):
    code = "INVALID_STATE"
