"""Shared application errors, independent of service assembly."""


class AppError(Exception):
    def __init__(self, message, status=400, *, code=None, receipt=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.receipt = receipt
