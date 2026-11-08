"""Run slow work (reports, exports, backups) off the GUI thread.

``TaskRunner`` lives in the GUI thread. Each task executes on Qt's thread pool and
reports back through signals connected to a ``TaskRunner`` slot, so the result and
error callbacks always run on the GUI thread, where touching widgets is safe.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from src.domain.exceptions import AppError
from src.logging_config import get_logger

log = get_logger(__name__)

UNEXPECTED_ERROR = "An unexpected error occurred. See the log for details."


class _Signals(QObject):
    succeeded = Signal(int, object)
    failed = Signal(int, str)


class _Task(QRunnable):
    def __init__(self, task_id: int, work: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self._task_id = task_id
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._work()
        except AppError as exc:
            self._signals.failed.emit(self._task_id, exc.message)
        except Exception:
            log.exception("background_task_failed")
            self._signals.failed.emit(self._task_id, UNEXPECTED_ERROR)
        else:
            self._signals.succeeded.emit(self._task_id, result)


class TaskRunner(QObject):
    """Submit callables; get ``on_done(result)`` or ``on_error(message)`` on the GUI thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable[[Any], None], Callable[[str], None]]] = {}
        self._signals = _Signals(self)
        self._signals.succeeded.connect(self._on_succeeded)
        self._signals.failed.connect(self._on_failed)
        self._pool = QThreadPool.globalInstance()

    @property
    def pending(self) -> int:
        return len(self._callbacks)

    def submit(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None],
    ) -> None:
        task_id = next(self._ids)
        self._callbacks[task_id] = (on_done, on_error)
        self._pool.start(_Task(task_id, work, self._signals))

    @Slot(int, object)
    def _on_succeeded(self, task_id: int, result: object) -> None:
        on_done, _ = self._callbacks.pop(task_id)
        on_done(result)

    @Slot(int, str)
    def _on_failed(self, task_id: int, message: str) -> None:
        _, on_error = self._callbacks.pop(task_id)
        on_error(message)
