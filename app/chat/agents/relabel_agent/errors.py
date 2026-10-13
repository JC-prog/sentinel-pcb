from typing import Any


class RelabelRefused(Exception):
    """A relabel the agent will not do, with the reason the user should hear - an unknown case, a
    label the model cannot output, nothing to confirm. `details` carry anything that helps them
    fix it (e.g. the valid labels)."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
