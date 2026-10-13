"""Root logger setup, mirroring the backend's app/config/logging_config.py so both services emit
the same shape of line into CloudWatch. Deliberately a copy, not a shared import - the two
services are packaged and deployed independently.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from inference_service.settings import settings

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_DATEFMT = "%Y-%m-%d %H:%M:%S"

_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__
) | {"message"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Idempotent - replaces the root handlers rather than appending, so calling it more than
    once never duplicates lines."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter()
        if settings.log_format == "json"
        else logging.Formatter(_CONSOLE_FORMAT, datefmt=_CONSOLE_DATEFMT)
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, settings.log_level))
