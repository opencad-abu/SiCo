"""Shared PDK boundary errors, independent of tool contracts."""


class PdkArgumentError(ValueError):
    pass


class PdkUnavailable(RuntimeError):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.data = data
