"""Structured logging: console, rotating JSON-lines file and an in-memory UI buffer.

``structlog`` produces the event dictionaries; the standard library handlers
decide where they go. Both ``structlog.get_logger()`` and plain
``logging.getLogger()`` calls (from third-party libraries) end up in the same
handlers with the same format.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from src.app.constants import LOG_BACKUP_COUNT, LOG_MAX_BYTES, UI_LOG_BUFFER_SIZE

_NOISY_LOGGERS = ("uvicorn.access", "httpx", "httpcore", "asyncio", "alembic.runtime.migration")


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A log record prepared for display in the GUI."""

    timestamp: datetime
    level: str
    logger: str
    message: str


LogListener = Callable[[LogEntry], None]


class UiLogBuffer(logging.Handler):
    """Keeps the most recent log entries in memory and notifies listeners.

    Listeners are invoked on the *emitting* thread; GUI code must marshal the
    call onto the Qt main thread (see ``ui.widgets.log_viewer``).
    """

    def __init__(self, capacity: int = UI_LOG_BUFFER_SIZE) -> None:
        super().__init__()
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._listeners: list[LogListener] = []
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created),
                level=record.levelname,
                logger=record.name,
                message=self.format(record),
            )
        except Exception:
            self.handleError(record)
            return
        with self._guard:
            self._entries.append(entry)
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(entry)

    def entries(self) -> list[LogEntry]:
        """Return a snapshot of the buffered entries, oldest first."""
        with self._guard:
            return list(self._entries)

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()

    def subscribe(self, listener: LogListener) -> None:
        with self._guard:
            self._listeners.append(listener)

    def unsubscribe(self, listener: LogListener) -> None:
        with self._guard:
            if listener in self._listeners:
                self._listeners.remove(listener)


def configure_logging(log_file: Path, level: str = "INFO") -> UiLogBuffer:
    """Configure logging for the whole process and return the UI buffer.

    Safe to call more than once (handlers from a previous call are replaced),
    which matters for tests.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    def formatter(renderer: Any, *, keep_timestamp: bool = True) -> logging.Formatter:
        processors: list[Any] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
        if not keep_timestamp:
            processors.append(_drop_keys("timestamp", "level", "logger"))
        processors.extend([structlog.processors.format_exc_info, renderer])
        return structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors, processors=processors
        )

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter(structlog.processors.JSONRenderer()))

    handlers: list[logging.Handler] = [file_handler]
    # Started from a desktop shortcut (pythonw.exe) there is no console at all.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter(structlog.dev.ConsoleRenderer(colors=False)))
        handlers.append(console_handler)

    ui_buffer = UiLogBuffer()
    ui_buffer.setFormatter(
        formatter(structlog.processors.KeyValueRenderer(key_order=["event"]), keep_timestamp=False)
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    handlers.append(ui_buffer)
    for handler in handlers:
        root.addHandler(handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return ui_buffer


def shutdown_logging() -> None:
    """Flush and close every handler."""
    logging.shutdown()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to ``name``."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def _drop_keys(*keys: str) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    """Build a processor that removes ``keys`` from the event dictionary."""

    def processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        for key in keys:
            event_dict.pop(key, None)
        return event_dict

    return processor
