"""Thread-safe pause / resume / stop signalling.

The GUI thread flips the flags; the automation loop (another thread) polls them
at safe boundaries. Nothing is ever killed: a stop is a request that the
session honours at its next checkpoint.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Final

from src.domain.exceptions import SessionCancelledError

POLL_SECONDS: Final = 0.1


class RunControl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._running = threading.Event()
        self._running.set()

    # -- commands (any thread) ---------------------------------------------------- #
    def pause(self) -> None:
        if not self._stop.is_set():
            self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def stop(self) -> None:
        """Request a stop; also releases anything waiting on a pause."""
        self._stop.set()
        self._running.set()

    # -- queries ---------------------------------------------------------------- #
    @property
    def is_paused(self) -> bool:
        return not self._running.is_set()

    @property
    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def raise_if_stopped(self) -> None:
        if self._stop.is_set():
            raise SessionCancelledError("The run was stopped.")

    # -- awaiting (automation thread) --------------------------------------------- #
    async def wait_while_paused(self) -> None:
        while not self._running.is_set():
            await asyncio.sleep(POLL_SECONDS)
        self.raise_if_stopped()

    async def sleep(self, seconds: float) -> None:
        """Sleep that wakes up promptly for a stop and holds still during a pause."""
        remaining = max(0.0, seconds)
        while remaining > 0:
            self.raise_if_stopped()
            await self.wait_while_paused()
            step = min(POLL_SECONDS, remaining)
            await asyncio.sleep(step)
            remaining -= step
        self.raise_if_stopped()
