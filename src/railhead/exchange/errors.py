"""One error type for the whole exchange, with a stable machine-readable code."""


class ExchangeError(Exception):
    """A rejected exchange command or record. ``code`` is stable; ``message`` is for people."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
