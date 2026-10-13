class CaseRefused(Exception):
    """A Case the agent will not create, with the reason the user should hear - nothing inspected
    to save, or the user has not confirmed yet."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
