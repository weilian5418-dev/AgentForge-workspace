"""Typed errors for D09 instance / session / eligibility flows."""


class InstanceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
