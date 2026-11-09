"""State machine of one automation session.

The happy path is a straight line; ``DWELLING`` and ``INTERACTING`` alternate;
any active state may fail, be stopped or be paused. Pausing remembers the state
it interrupted and resuming returns exactly there.

Two shapes exist. A search journey starts Chromium as part of ``OPENING_SEARCH``
- Chromium is what performs the search - then types the keyword (``ENTERING_KEYWORD``),
reads a page (``SEARCHING``), walks further pages while the target is missing
(``PAGINATING_RESULTS``), and clicks the result it found (``CLICKING_RESULT``)
instead of navigating to a URL. A direct visit (a local site Google cannot list)
opens the target with no search state at all.

``PAGINATING_RESULTS`` and ``CLICKING_RESULT`` are new; older sessions that never
paged stay readable because every state the old flow used is still legal.

The machine only validates and records transitions. Persisting them and
notifying the GUI is done by the ``on_change`` callback.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from src.database.base import utcnow
from src.domain.enums import SessionState as S
from src.domain.exceptions import InvalidStateTransitionError

TERMINAL_STATES: Final = frozenset({S.COMPLETED, S.FAILED, S.STOPPED})

_FORWARD: Final[dict[S, frozenset[S]]] = {
    S.IDLE: frozenset({S.INITIALIZING}),
    # A journey either searches (and therefore opens the search page first) or has
    # nothing to ask: a direct visit knows the target is fine to open, so it goes
    # straight to TARGET_FOUND.
    S.INITIALIZING: frozenset({S.STARTING_BROWSER, S.SEARCHING, S.OPENING_SEARCH, S.TARGET_FOUND}),
    # TARGET_FOUND is the shortcut of direct-visit sessions: nothing had to be found.
    S.STARTING_BROWSER: frozenset({S.OPENING_SEARCH, S.TARGET_FOUND, S.OPENING_TARGET}),
    # The search page is opened, the keyword is typed, the page is read.
    S.OPENING_SEARCH: frozenset({S.ENTERING_KEYWORD, S.SEARCHING}),
    S.ENTERING_KEYWORD: frozenset({S.SEARCHING}),
    # Reading another page is part of the search, not a step of its own: the walk
    # repeats SEARCHING until the target turns up or the pages run out.
    S.SEARCHING: frozenset({S.ANALYZING_RESULTS, S.PAGINATING_RESULTS}),
    S.PAGINATING_RESULTS: frozenset({S.SEARCHING, S.ANALYZING_RESULTS}),
    S.ANALYZING_RESULTS: frozenset({S.TARGET_FOUND, S.TARGET_NOT_FOUND}),
    # The click on the result *is* the visit, so OPENING_TARGET follows it and only
    # confirms where the tab ended up.
    S.TARGET_FOUND: frozenset({S.CLICKING_RESULT, S.OPENING_TARGET, S.STARTING_BROWSER}),
    S.CLICKING_RESULT: frozenset({S.OPENING_TARGET}),
    S.TARGET_NOT_FOUND: frozenset({S.CAPTURING_RESULT}),
    S.OPENING_TARGET: frozenset({S.DWELLING}),
    S.DWELLING: frozenset({S.INTERACTING, S.CAPTURING_RESULT}),
    S.INTERACTING: frozenset({S.DWELLING}),
    S.CAPTURING_RESULT: frozenset({S.COMPLETED}),
}

# States that can be interrupted by fail / stop / pause.
ACTIVE_STATES: Final = frozenset(_FORWARD) - {S.IDLE}

ChangeListener = Callable[[S, S], None]


class SessionStateMachine:
    """Validates the order in which a session moves through its states."""

    def __init__(self, on_change: ChangeListener | None = None) -> None:
        self._state = S.IDLE
        self._before_pause: S | None = None
        self._on_change = on_change
        self.history: list[tuple[S, datetime]] = [(S.IDLE, utcnow())]

    def set_listener(self, on_change: ChangeListener | None) -> None:
        """Register the callback invoked after every transition."""
        self._on_change = on_change

    @property
    def state(self) -> S:
        return self._state

    @property
    def is_paused(self) -> bool:
        return self._state is S.PAUSED

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    @staticmethod
    def allowed_from(state: S) -> frozenset[S]:
        """States reachable from ``state`` in one step (excluding pause/resume)."""
        targets = set(_FORWARD.get(state, frozenset()))
        if state in ACTIVE_STATES:
            targets |= {S.FAILED, S.STOPPED}
        return frozenset(targets)

    def can_transition(self, target: S) -> bool:
        return target in self.allowed_from(self._state)

    def transition(self, target: S) -> None:
        """Move to ``target`` or raise :class:`InvalidStateTransitionError`."""
        if self._state is S.PAUSED and target in {S.FAILED, S.STOPPED}:
            self._before_pause = None
            self._enter(target)
            return
        if not self.can_transition(target):
            raise InvalidStateTransitionError(
                f"Cannot go from {self._state.value} to {target.value}.",
                source=self._state.value,
                target=target.value,
            )
        self._enter(target)

    def pause(self) -> None:
        if self._state not in ACTIVE_STATES:
            raise InvalidStateTransitionError(
                f"A session in state {self._state.value} cannot be paused.",
                source=self._state.value,
                target=S.PAUSED.value,
            )
        self._before_pause = self._state
        self._enter(S.PAUSED)

    def resume(self) -> S:
        """Leave ``PAUSED`` and return the state that was interrupted."""
        if self._state is not S.PAUSED or self._before_pause is None:
            raise InvalidStateTransitionError(
                "Only a paused session can be resumed.", source=self._state.value
            )
        previous, self._before_pause = self._before_pause, None
        self._enter(previous)
        return previous

    def fail(self) -> None:
        self._finish(S.FAILED)

    def stop(self) -> None:
        self._finish(S.STOPPED)

    def _finish(self, target: S) -> None:
        if self.is_terminal:
            return
        if self._state is S.IDLE:
            raise InvalidStateTransitionError(
                "A session that has not started cannot finish.", source=S.IDLE.value
            )
        self.transition(target)

    def _enter(self, target: S) -> None:
        previous, self._state = self._state, target
        self.history.append((target, utcnow()))
        if self._on_change is not None:
            self._on_change(previous, target)
