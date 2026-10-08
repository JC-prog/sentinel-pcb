class SampleRefused(Exception):
    """A lookup the agent cannot answer, with the reason the user should hear - an unknown sample or
    run, nothing stored yet, the store unreachable."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
