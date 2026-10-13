class ReviewRefused(Exception):
    """A case review the agent will not do, with the reason the user should hear - an unknown case,
    a case that is not awaiting review, nothing to confirm."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
