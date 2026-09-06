"""Configures the root logger once at app startup (see app/main.py's lifespan) - every
`logging.getLogger(__name__)` call across the app inherits this instead of needing its own setup.

Format is env-driven (settings.log_format): a human-readable console format for local dev, or one
JSON object per line in production, so ECS Fargate's `awslogs` log driver ships something
CloudWatch can actually query. Both write to the same place (stdout) - only the format changes,
so there's no new infrastructure (no Loki/ELK stack) and no file-writing logic in the app itself.

Does not touch Uvicorn's own "uvicorn"/"uvicorn.access"/"uvicorn.error" loggers - those configure
their own handlers independently and keep their existing request-log format.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config.settings import settings

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Every attribute a plain LogRecord already has - used to tell "extra" fields (e.g.
# logger.info(..., extra={"status_code": 200}), as app/main.py's request-logging middleware
# does) apart from the record's own built-in attributes, so those extras can be surfaced as their
# own JSON keys generically, not just for this one call site.
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
    """Idempotent - always replaces the root logger's handlers rather than appending to them, so
    calling this more than once (e.g. if it's ever invoked from more than one place) never
    duplicates log lines."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter()
        if settings.log_format == "json"
        else logging.Formatter(_CONSOLE_FORMAT, datefmt=_CONSOLE_DATEFMT)
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, settings.log_level))
