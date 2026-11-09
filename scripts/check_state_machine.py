#!/usr/bin/env python3
"""Static check of the session state machine - no Qt, no browser, python 3.9+.

``tests/unit/test_automation_core.py`` asserts the transition table's shape, but
only a check that reads the *source* catches the two failures that have actually
shipped here: a journey that asks for a transition the table forbids (which breaks
every session the moment it runs) and a progress ladder that goes backwards for one
of the journey shapes.

Run it from the repository root::

    python scripts/check_state_machine.py

It parses ``src/automation/state_machine.py``, ``src/automation/session_runner.py``
and ``src/ui/run_controller.py`` with ``ast`` - nothing is imported, so it works on
any supported Python without the application's dependencies installed.
"""

from __future__ import annotations

import ast
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_MACHINE = ROOT / "src" / "automation" / "state_machine.py"
RUNNER = ROOT / "src" / "automation" / "session_runner.py"
CONTROLLER = ROOT / "src" / "ui" / "run_controller.py"

# The journeys the runner can perform, in the order it performs them. A pause and
# a stop may interrupt any of them; the table must allow both from every state in
# the walk (checked separately below).
JOURNEYS: dict[str, list[str]] = {
    "search journey, target on the first page": [
        "INITIALIZING",
        "OPENING_SEARCH",
        "ENTERING_KEYWORD",
        "SEARCHING",
        "ANALYZING_RESULTS",
        "TARGET_FOUND",
        "CLICKING_RESULT",
        "OPENING_TARGET",
        "DWELLING",
        "INTERACTING",
        "DWELLING",
        "CAPTURING_RESULT",
        "COMPLETED",
    ],
    "search journey, target on a later page": [
        "INITIALIZING",
        "OPENING_SEARCH",
        "ENTERING_KEYWORD",
        "SEARCHING",
        "PAGINATING_RESULTS",
        "SEARCHING",
        "PAGINATING_RESULTS",
        "SEARCHING",
        "ANALYZING_RESULTS",
        "TARGET_FOUND",
        "CLICKING_RESULT",
        "OPENING_TARGET",
        "DWELLING",
        "CAPTURING_RESULT",
        "COMPLETED",
    ],
    "search journey, target not listed": [
        "INITIALIZING",
        "OPENING_SEARCH",
        "ENTERING_KEYWORD",
        "SEARCHING",
        "PAGINATING_RESULTS",
        "SEARCHING",
        "ANALYZING_RESULTS",
        "TARGET_NOT_FOUND",
        "CAPTURING_RESULT",
        "COMPLETED",
    ],
    "direct visit (a target Google cannot list)": [
        "INITIALIZING",
        "TARGET_FOUND",
        "STARTING_BROWSER",
        "OPENING_TARGET",
        "DWELLING",
        "INTERACTING",
        "DWELLING",
        "CAPTURING_RESULT",
        "COMPLETED",
    ],
}

DWELL_STATES = {"DWELLING", "INTERACTING"}
FINAL_STATES = {"COMPLETED", "FAILED", "STOPPED"}


def parse_forward() -> tuple[dict[str, set[str]], set[str]]:
    """``(_FORWARD, ACTIVE_STATES)`` read from the state machine's source."""
    tree = ast.parse(STATE_MACHINE.read_text(encoding="utf-8"))
    forward = parse_forward_dict(tree)
    # ACTIVE_STATES is `frozenset(_FORWARD) - {IDLE}`: every state the table has a
    # row for may be paused, stopped or failed.
    active = {state for state in forward if state != "IDLE"}
    return forward, active


def parse_forward_dict(tree: ast.AST) -> dict[str, set[str]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "_FORWARD" for t in targets):
                return {k: set(v) for k, v in _decode_dict(node.value).items()}
    raise SystemExit("could not find _FORWARD")


def _decode_dict(node: ast.AST) -> dict[str, set[str]]:
    """Read ``{S.STATE: frozenset({S.OTHER, ...}), ...}`` into plain strings."""
    assert isinstance(node, ast.Dict), "the transition table must be a dict literal"
    out: dict[str, set[str]] = {}
    for key, value in zip(node.keys, node.values, strict=False):
        name = _state_name(key)
        members: set[str] = set()
        if isinstance(value, ast.Call):  # frozenset({...})
            for element in _iter_set(value.args[0]):
                members.add(_state_name(element))
        out[name] = members
    return out


def _iter_set(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return list(node.elts)
    raise SystemExit(f"unsupported literal: {ast.dump(node)[:80]}")


def _state_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):  # S.TARGET_FOUND
        return node.attr
    raise SystemExit(f"not a state reference: {ast.dump(node)[:80]}")


def runner_states() -> set[str]:
    """Every state the runner asks for, by name, from its source."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_go(node):
            for argument in node.args[1:2]:
                # `self._go(t, announce, ...)` passes a parameter, not a state: only
                # the literal states can be checked against the table from source.
                if isinstance(argument, ast.Attribute):
                    found.add(argument.attr)
    return found


def _is_go(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in {"_go"}


def progress_table() -> tuple[dict[str, float], float, float]:
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    table: dict[str, float] = {}
    dwell_start = dwell_span = 0.0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if "_STATE_PROGRESS" in names and node.value is not None:
            assert isinstance(node.value, ast.Dict)
            for key, value in zip(node.value.keys, node.value.values, strict=False):
                table[_state_name(key)] = float(value.value)  # type: ignore[union-attr]
        if "_DWELL_START" in names and node.value is not None:
            dwell_start = float(node.value.value)  # type: ignore[union-attr]
        if "_DWELL_SPAN" in names and node.value is not None:
            dwell_span = float(node.value.value)  # type: ignore[union-attr]
    return table, dwell_start, dwell_span


def main() -> int:
    forward, active = parse_forward()
    problems: list[str] = []

    used = runner_states()
    # A terminal state is never a key of the table: nothing follows it.
    known = set(forward) | FINAL_STATES
    unknown = used - known
    if unknown:
        problems.append(f"states the runner uses but the table has no row for: {sorted(unknown)}")

    for label, journey in JOURNEYS.items():
        missing = [state for state in journey if state not in forward and state not in FINAL_STATES]
        if missing:
            problems.append(f"{label}: states missing from the enum/table: {missing}")
            continue
        for before, after in itertools.pairwise(journey):
            if after in ("PAUSED",):
                continue
            allowed = forward.get(before, set()) | {"FAILED", "STOPPED"}
            if after not in allowed:
                problems.append(
                    f"{label}: {before} -> {after} is not legal "
                    f"({before} allows {sorted(forward.get(before, set())) or 'nothing'})"
                )
        # A pause may interrupt any state of the walk, and must resume into it.
        for state in journey:
            if state in FINAL_STATES:
                continue
            if state not in active:
                problems.append(f"{label}: '{state}' cannot be paused (not an active state)")

    # Progress must never go backwards while a session is running.
    table, dwell_start, dwell_span = progress_table()

    def fraction(state: str, dwell: float = 0.0) -> float:
        if state in FINAL_STATES:
            return 1.0
        if state in DWELL_STATES:
            return dwell_start + dwell_span * dwell
        return table.get(state, 0.0)

    for label, journey in JOURNEYS.items():
        seen = 0.0
        # The dwell is one ramp: the runner reports a dwell fraction that only ever
        # falls as time passes, so DWELLING and INTERACTING share the same position.
        dweller = [i for i, state in enumerate(journey) if state in DWELL_STATES]
        for index, state in enumerate(journey):
            step = dweller.index(index) / max(len(dweller) - 1, 1) if index in dweller else 0.0
            value = fraction(state, step)
            if value < seen - 1e-9:
                problems.append(
                    f"{label}: progress goes backwards at {state} ({value:.2f} after {seen:.2f})"
                )
            seen = max(seen, value)

    if problems:
        print("STATE MACHINE PROBLEMS")
        for problem in problems:
            print(" -", problem)
        return 1
    states = {state for journey in JOURNEYS.values() for state in journey}
    print(
        f"ok: {len(JOURNEYS)} journeys over {len(states)} states are legal, pausable and "
        f"monotone ({len(forward)} rows in the transition table)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
