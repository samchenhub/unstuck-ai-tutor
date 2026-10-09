class IngestError(Exception):
    """An import failure with a stable machine-readable code and a user-facing message."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
