"""Configures the root logger once at app startup (see app/main.py's lifespan) - every
`logging.getLogger(__name__)` call across the app inherits this instead of needing its own setup.

Format is env-driven (settings.log_format): a human-readable console format for local dev, or one
JSON object per line in production, so ECS Fargate's `awslogs` log driver ships something
CloudWatch can actually query. Both write to stdout; settings.log_to_file additionally writes the
same lines to a rotating file under settings.log_dir - opt-in, since not every deployment needs
it and it isn't free in a container (see settings.py's log_to_file for the same host-visibility
caveat chat_upload_dir already has).

Does not touch Uvicorn's own "uvicorn"/"uvicorn.access"/"uvicorn.error" loggers - those configure
their own handlers independently and keep their existing request-log format.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config.settings import settings

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_CONSOLE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 10 MiB per file, 5 backups kept (app.log, app.log.1, ... app.log.5) - bounded, not a growing
# file someone eventually has to notice and clean up by hand.
_LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 5

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


def _formatter() -> logging.Formatter:
    return (
        _JsonFormatter()
        if settings.log_format == "json"
        else logging.Formatter(_CONSOLE_FORMAT, datefmt=_CONSOLE_DATEFMT)
    )


def configure_logging() -> None:
    """Idempotent - always replaces the root logger's handlers rather than appending to them, so
    calling this more than once (e.g. if it's ever invoked from more than one place) never
    duplicates log lines."""

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if settings.log_to_file:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=_LOG_FILE_MAX_BYTES,
                backupCount=_LOG_FILE_BACKUP_COUNT,
            )
        )

    for handler in handlers:
        handler.setFormatter(_formatter())

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(getattr(logging, settings.log_level))
