"""State machine of one automation session.

The happy path is a straight line; ``DWELLING`` and ``INTERACTING`` alternate;
any active state may fail, be stopped or be paused. Pausing remembers the state
it interrupted and resuming returns exactly there.

Three shapes exist, because a scenario chooses where its ranking comes from. The
API channel asks the provider first (``SEARCHING`` -> ``ANALYZING_RESULTS``) and
only then starts a browser, so a site that is not ranked costs no page load. The
browser channel starts Chromium as part of ``OPENING_SEARCH`` and reads the
results page there, so it never reports ``STARTING_BROWSER`` again. A direct visit
(Google Research mode, or a local site Google cannot list) opens the target with no
search state. ``ENTERING_KEYWORD`` remains in the enum for sessions recorded by
earlier versions, and stays legal so those stay readable.

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
    # A journey either reads the results page first (browser channel), asks the
    # provider first (API channel), or has nothing to ask: a direct visit knows the
    # target is fine to open (it was observed already, or Google cannot list it), so
    # it goes straight to TARGET_FOUND.
    S.INITIALIZING: frozenset({S.STARTING_BROWSER, S.SEARCHING, S.OPENING_SEARCH, S.TARGET_FOUND}),
    # TARGET_FOUND is the shortcut of direct-visit sessions: the target was already observed.
    S.STARTING_BROWSER: frozenset({S.OPENING_SEARCH, S.TARGET_FOUND, S.OPENING_TARGET}),
    # The browser channel reads the page it just opened; the old flow typed the
    # keyword into the box first and stays legal for sessions recorded that way.
    S.OPENING_SEARCH: frozenset({S.ENTERING_KEYWORD, S.SEARCHING}),
    S.ENTERING_KEYWORD: frozenset({S.SEARCHING}),
    S.SEARCHING: frozenset({S.ANALYZING_RESULTS}),
    S.ANALYZING_RESULTS: frozenset({S.TARGET_FOUND, S.TARGET_NOT_FOUND}),
    # The provider already found the target; the browser starts after that check.
    S.TARGET_FOUND: frozenset({S.OPENING_TARGET, S.STARTING_BROWSER}),
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
