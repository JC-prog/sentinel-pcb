import json
import logging
from collections.abc import Generator

import pytest

from app.config.logging_config import configure_logging
from app.config.settings import settings


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Generator[None, None, None]:
    """configure_logging() mutates the root logger's handlers/level globally - restore the
    original state after each test so this file can't leak configuration into other test
    modules (e.g. one that relies on pytest's own log capture)."""

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def _make_record(message: str = "hello world") -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def _formatter() -> logging.Formatter:
    handler = logging.getLogger().handlers[0]
    assert handler.formatter is not None
    return handler.formatter


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()
    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_respects_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_level", "INFO")
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_console_format_is_human_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "console")
    configure_logging()

    output = _formatter().format(_make_record("hello world"))

    assert "hello world" in output
    assert "INFO" in output
    assert "app.test" in output


def test_json_format_is_valid_json_with_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    configure_logging()

    payload = json.loads(_formatter().format(_make_record("hello world")))

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


def test_json_format_surfaces_extra_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    configure_logging()

    record = _make_record("request handled")
    record.status_code = 200
    record.duration_ms = 12.3

    payload = json.loads(_formatter().format(record))

    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.3


def test_json_format_includes_exception_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "log_format", "json")
    configure_logging()

    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record("something failed")
        record.exc_info = sys.exc_info()
        payload = json.loads(_formatter().format(record))

    assert "boom" in payload["exception"]
