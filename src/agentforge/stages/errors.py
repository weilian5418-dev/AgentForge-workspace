"""Typed errors for D10 stage gate flows."""


class StageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
