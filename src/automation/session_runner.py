"""Runs scenarios: one session per iteration, driven by the state machine.

The runner is written against :class:`AutomationEngine` and owns everything
that is not browser-specific: ordering (state machine), the event timeline,
the pausable dwell clock, stop handling, error classification and persistence.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Callable
from typing import Any, Final

from src.automation.control import RunControl
from src.automation.engine import AutomationEngine
from src.automation.interaction_service import (
    ACTION_LABELS,
    ScheduledAction,
    build_schedule,
    choose_dwell,
)
from src.automation.models import (
    ResultItem,
    RunSummary,
    SessionPlan,
    SessionResult,
)
from src.automation.observer import RunObserver, SafeObserver
from src.automation.recorder import SessionRecorder
from src.automation.state_machine import SessionStateMachine
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from src.domain.exceptions import (
    AppError,
    BrowserLaunchError,
    SafetyViolationError,
    SessionCancelledError,
)
from src.logging_config import get_logger
from src.services.safety_service import DomainSafetyService

log = get_logger(__name__)

DWELL_TICK_SECONDS: Final = 0.25
PREVIEW_EVERY_TICKS: Final = 4  # about one frame per second while dwelling
PREVIEW_STATES: Final = frozenset(
    {
        SessionState.ENTERING_KEYWORD,
        SessionState.ANALYZING_RESULTS,
        SessionState.DWELLING,
        SessionState.INTERACTING,
        SessionState.CAPTURING_RESULT,
    }
)
SEED_BITS: Final = 32

SeedSource = Callable[[], int]
EngineFactory = Callable[[SessionPlan], AutomationEngine]
SearchConfigurator = Callable[[SessionPlan], None]


def random_seed() -> int:
    return random.SystemRandom().getrandbits(SEED_BITS)


class _Tracker:
    """Per-iteration state shared by the runner's steps."""

    def __init__(
        self,
        session_id: int,
        plan: SessionPlan,
        rng: random.Random,
        machine: SessionStateMachine,
    ) -> None:
        self.session_id = session_id
        self.plan = plan
        self.rng = rng
        self.machine = machine
        self.target_found: bool | None = None
        self.target_position: int | None = None
        self.planned_dwell: float | None = None
        self.actual_dwell: float | None = None
        self.scroll_actions = 0


class SessionRunner:
    """Executes iterations of a plan against an engine."""

    def __init__(
        self,
        engine: AutomationEngine,
        recorder: SessionRecorder,
        safety: DomainSafetyService,
        control: RunControl,
        observer: RunObserver | None = None,
        configure_search: SearchConfigurator | None = None,
        seed_source: SeedSource = random_seed,
    ) -> None:
        self._engine = engine
        self._recorder = recorder
        self._safety = safety
        self._control = control
        self._observer = SafeObserver(observer)
        self._configure_search = configure_search
        self._seed_source = seed_source

    # -- one scenario ------------------------------------------------------------- #
    async def run_plan(self, plan: SessionPlan, run_id: str | None = None) -> RunSummary:
        """Run every iteration of ``plan``. A failed iteration does not end the run."""
        run_id = run_id or str(uuid.uuid4())
        summary = RunSummary()
        try:
            for iteration in range(1, plan.iterations + 1):
                if self._control.is_stopped:
                    break
                result = await self.run_iteration(plan, iteration, run_id)
                summary.add(result)
                if result.status is SessionStatus.STOPPED:
                    break
                if result.error_type == BrowserLaunchError.__name__:
                    break  # every further iteration would fail for the same reason
                if iteration < plan.iterations:
                    await self._wait_between_iterations(plan.delay_between_iterations)
        except SessionCancelledError:
            pass  # stopped while waiting between iterations
        finally:
            await self._engine.close()
        return summary

    async def _wait_between_iterations(self, seconds: int) -> None:
        if seconds <= 0:
            return
        self._observer.on_waiting(float(seconds))
        await self._control.sleep(seconds)

    # -- one iteration ------------------------------------------------------------- #
    async def run_iteration(self, plan: SessionPlan, iteration: int, run_id: str) -> SessionResult:
        seed = self._seed_source()
        rng = random.Random(seed)
        machine = SessionStateMachine()
        session_id = self._recorder.create(plan, run_id, iteration, seed, {})
        tracker = _Tracker(session_id, plan, rng, machine)
        machine.set_listener(lambda _old, new: self._recorder.set_state(session_id, new))
        self._observer.on_session_started(session_id, iteration, plan.iterations)

        status = SessionStatus.COMPLETED
        error: Exception | None = None
        try:
            await self._execute(tracker)
            if tracker.target_found is False:
                status = SessionStatus.TARGET_NOT_FOUND
        except SessionCancelledError:
            status = SessionStatus.STOPPED
            self._finish_machine(tracker, stopped=True)
            self._event(tracker, EventType.SESSION_STOPPED, "Stopped by the user.")
        except Exception as exc:
            status, error = SessionStatus.FAILED, exc
            await self._record_failure(tracker, exc)
        finally:
            await self._engine.end_session()

        return self._finish(tracker, iteration, status, error)

    async def _execute(self, t: _Tracker) -> None:
        plan, engine = t.plan, self._engine

        await self._go(t, SessionState.INITIALIZING, "Checking safety rules")
        self._event(
            t, EventType.SESSION_STARTED, f"keyword '{plan.keyword}' on {plan.target_domain}"
        )
        self._safety.check_url(plan.target_url)
        if plan.mode == "direct":
            await self._execute_direct(t)
            return
        self._safety.check_url(plan.search_url)
        if self._configure_search is not None:
            self._configure_search(plan)

        await self._go(t, SessionState.STARTING_BROWSER, "Starting the browser")
        info = await engine.start_session(plan)
        self._recorder.update(t.session_id, browser_info=info)
        note = (
            ""
            if info.get("network_fence", True)
            else " (proxy mode: redirects checked after the fact)"
        )
        self._event(t, EventType.BROWSER_STARTED, f"Chromium {info.get('version', '')}{note}", info)

        await self._go(t, SessionState.OPENING_SEARCH, "Opening the search page")
        url = await engine.navigate(plan.search_url)
        self._page(url)
        self._event(t, EventType.SEARCH_PAGE_OPENED, url)

        await self._go(t, SessionState.ENTERING_KEYWORD, f"Typing '{plan.keyword}'")
        await engine.fill_search(plan.keyword)
        self._event(t, EventType.KEYWORD_ENTERED, plan.keyword)

        await self._go(t, SessionState.SEARCHING, "Submitting the search")
        await engine.submit_search()
        self._page(engine.current_url)
        self._event(t, EventType.SEARCH_SUBMITTED, engine.current_url)

        await self._go(t, SessionState.ANALYZING_RESULTS, "Reading the results")
        results = await engine.get_results()
        self._recorder.update(t.session_id, results_count=len(results))
        self._event(
            t, EventType.RESULTS_RECEIVED, f"{len(results)} results", {"count": len(results)}
        )
        await self._screenshot(t, ScreenshotKind.SEARCH_RESULTS)

        item = engine.find_target(results, plan.target_url)
        if item is None:
            await self._target_not_found(t)
            return
        await self._open_and_dwell(t, item)

    async def _execute_direct(self, t: _Tracker) -> None:
        """Google Research mode: the target was observed by a real search; open it directly."""
        plan, engine = t.plan, self._engine
        await self._go(t, SessionState.STARTING_BROWSER, "Starting the browser")
        info = await engine.start_session(plan)
        self._recorder.update(t.session_id, browser_info=info)
        self._event(t, EventType.BROWSER_STARTED, f"Chromium {info.get('version', '')}", info)

        position = plan.observed_position
        t.target_found, t.target_position = True, position
        self._recorder.update(
            t.session_id,
            target_found=True,
            target_position=position,
            results_count=plan.observed_result_count,
        )
        self._observer.on_target_position(position)
        self._event(
            t,
            EventType.SEARCH_OBSERVED,
            f"{plan.provider}: '{plan.keyword}' -> {plan.target_domain} at position {position}",
            {
                "provider": plan.provider,
                "observation_id": plan.observation_id,
                "position": position,
            },
        )
        await self._go(t, SessionState.TARGET_FOUND, f"Observed at position {position}")
        self._event(t, EventType.TARGET_FOUND, f"position={position}", {"position": position})

        await self._go(t, SessionState.OPENING_TARGET, "Opening the target directly")
        url = await engine.open_url(plan.target_url)
        self._page(url)
        self._event(t, EventType.TARGET_OPENED, url)
        await self._screenshot(t, ScreenshotKind.TARGET_OPENED)
        await self._finish_visit(t)

    async def _target_not_found(self, t: _Tracker) -> None:
        await self._go(t, SessionState.TARGET_NOT_FOUND, f"{t.plan.target_domain} is not listed")
        t.target_found = False
        self._observer.on_target_position(None)
        self._recorder.update(t.session_id, target_found=False)
        self._event(t, EventType.TARGET_NOT_FOUND, t.plan.target_domain)
        await self._go(t, SessionState.CAPTURING_RESULT, "Saving the result")
        await self._screenshot(t, ScreenshotKind.SESSION_END)
        await self._go(t, SessionState.COMPLETED, "Target not found")
        self._event(t, EventType.SESSION_COMPLETED, "Completed: target not found")

    async def _open_and_dwell(self, t: _Tracker, item: ResultItem) -> None:
        engine = self._engine
        await self._go(t, SessionState.TARGET_FOUND, f"Target found at position {item.position}")
        t.target_found, t.target_position = True, item.position
        self._observer.on_target_position(item.position)
        self._recorder.update(t.session_id, target_found=True, target_position=item.position)
        self._event(
            t, EventType.TARGET_FOUND, f"position={item.position}", {"position": item.position}
        )

        await self._go(t, SessionState.OPENING_TARGET, "Opening the target")
        url = await engine.open_target(item)
        self._page(url)
        self._event(t, EventType.TARGET_OPENED, url)
        await self._screenshot(t, ScreenshotKind.TARGET_OPENED)
        await self._finish_visit(t)

    async def _finish_visit(self, t: _Tracker) -> None:
        """Dwell on the opened page, capture the result and complete the session."""
        await self._dwell(t)
        await self._go(t, SessionState.CAPTURING_RESULT, "Saving the result")
        await self._screenshot(t, ScreenshotKind.SESSION_END)
        await self._go(t, SessionState.COMPLETED, "Completed")
        self._event(t, EventType.SESSION_COMPLETED, "Completed")

    # -- dwell ----------------------------------------------------------------------- #
    async def _dwell(self, t: _Tracker) -> None:
        """Stay on the page for the chosen time, interacting on schedule.

        Time spent paused is not counted; time spent in an interaction is.
        """
        plan = t.plan
        planned = choose_dwell(plan.min_dwell_seconds, plan.max_dwell_seconds, t.rng)
        actions = plan.max_scroll_actions if plan.scroll_enabled else 0
        schedule = build_schedule(planned, actions, t.rng)
        t.planned_dwell = planned
        self._recorder.update(t.session_id, planned_dwell_seconds=planned)
        self._recorder.merge_snapshot(
            t.session_id,
            {
                "chosen_dwell_seconds": planned,
                "scheduled_actions": [
                    {"at": action.at_seconds, "kind": action.kind.value} for action in schedule
                ],
            },
        )

        await self._go(t, SessionState.DWELLING, f"Dwelling for {planned:g} s")
        self._event(
            t,
            EventType.DWELL_STARTED,
            f"{planned:g} s planned",
            {"planned_seconds": planned, "actions": len(schedule)},
        )
        self._observer.on_dwell_tick(planned, planned)

        elapsed = 0.0
        pending = list(schedule)
        ticks = 0
        while elapsed < planned:
            step = min(DWELL_TICK_SECONDS, planned - elapsed)
            await asyncio.sleep(step)
            await self._checkpoint(t)
            elapsed += step
            while pending and pending[0].at_seconds <= elapsed:
                action = pending.pop(0)
                elapsed += await self._interact(t, action)
            self._observer.on_dwell_tick(max(planned - elapsed, 0.0), planned)
            ticks += 1
            if ticks % PREVIEW_EVERY_TICKS == 0:
                await self._preview()

        t.actual_dwell = round(elapsed, 2)
        self._event(
            t,
            EventType.DWELL_COMPLETED,
            f"{t.actual_dwell:g} s",
            {"planned_seconds": planned, "actual_seconds": t.actual_dwell},
        )

    async def _interact(self, t: _Tracker, action: ScheduledAction) -> float:
        """Perform one scheduled interaction; returns the time it took."""
        await self._go(t, SessionState.INTERACTING, ACTION_LABELS[action.kind])
        started = time.monotonic()
        outcome = await self._engine.perform_safe_interaction(action, t.rng)
        took = time.monotonic() - started
        if outcome.event_type == EventType.SCROLL.value:
            t.scroll_actions += 1
            self._recorder.update(t.session_id, scroll_actions=t.scroll_actions)
        self._page(self._engine.current_url)
        self._event(t, EventType(outcome.event_type), outcome.message, outcome.data)
        await self._go(t, SessionState.DWELLING, "Dwelling")
        return took

    # -- state, events, pause --------------------------------------------------------- #
    async def _checkpoint(self, t: _Tracker) -> None:
        """Honour stop and pause requests at a safe boundary."""
        self._control.raise_if_stopped()
        self._verify_scope()
        if not self._control.is_paused:
            return
        t.machine.pause()
        self._observer.on_state_changed(SessionState.PAUSED, "Paused")
        self._event(t, EventType.PAUSED, "Paused")
        await self._control.wait_while_paused()  # raises SessionCancelledError on stop
        resumed = t.machine.resume()
        self._observer.on_state_changed(resumed, "Resumed")
        self._event(t, EventType.RESUMED, "Resumed")

    def _verify_scope(self) -> None:
        """Abort if the page we are on is no longer approved (e.g. approval revoked).

        The allowlist is read from the database on every check, so revoking a
        site takes effect within one dwell tick even when the page makes no
        further requests.
        """
        url = self._engine.current_url
        if url.startswith(("http://", "https://")):
            self._safety.check_url(url)

    async def _go(self, t: _Tracker, state: SessionState, message: str) -> None:
        await self._checkpoint(t)
        self._report_blocked(t)
        t.machine.transition(state)
        self._observer.on_state_changed(state, message)
        if state in PREVIEW_STATES:
            await self._preview()

    async def _preview(self) -> None:
        """Send the observer a small picture of the page (live view)."""
        frame = await self._engine.capture_preview()
        if frame:
            self._observer.on_preview(frame)

    def _report_blocked(self, t: _Tracker) -> None:
        """Put requests the safety guard blocked on the timeline."""
        for item in self._engine.drain_blocked():
            self._event(t, EventType.SAFETY_BLOCKED, f"blocked {item['url']}", item)

    def _event(
        self,
        t: _Tracker,
        event_type: EventType,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self._recorder.add_event(t.session_id, event_type, message, t.machine.state, data)
        self._observer.on_event(event_type.value, message, data or {})

    def _page(self, url: str) -> None:
        if url:
            self._observer.on_page_changed(url)

    async def _screenshot(self, t: _Tracker, kind: ScreenshotKind) -> None:
        info = await self._engine.capture_screenshot(kind, t.session_id, t.plan.keyword)
        if info is not None:
            self._event(t, EventType.SCREENSHOT_CAPTURED, f"{kind.value}: {info['file']}", info)

    # -- failure and completion ------------------------------------------------------- #
    async def _record_failure(self, t: _Tracker, exc: Exception) -> None:
        self._report_blocked(t)
        if isinstance(exc, SafetyViolationError):
            self._event(t, EventType.SAFETY_BLOCKED, exc.message, dict(exc.context))
        if isinstance(exc, AppError):
            log.warning("session_failed", session_id=t.session_id, error=exc.message)
        else:
            log.exception("session_crashed", session_id=t.session_id)
        message = exc.message if isinstance(exc, AppError) else f"Unexpected error: {exc}"
        self._event(t, EventType.ERROR, message, {"type": type(exc).__name__})
        await self._screenshot(t, ScreenshotKind.ERROR)
        self._finish_machine(t, stopped=False)
        self._event(t, EventType.SESSION_FAILED, message)

    def _finish_machine(self, t: _Tracker, stopped: bool) -> None:
        if t.machine.state is SessionState.IDLE or t.machine.is_terminal:
            return
        if stopped:
            t.machine.stop()
        else:
            t.machine.fail()
        final = t.machine.state
        self._observer.on_state_changed(final, final.value.title())

    def _finish(
        self,
        t: _Tracker,
        iteration: int,
        status: SessionStatus,
        error: Exception | None,
    ) -> SessionResult:
        if t.machine.is_terminal:
            state = t.machine.state
        else:  # failed before the machine ever started
            state = SessionState.STOPPED if status is SessionStatus.STOPPED else SessionState.FAILED
        error_type = type(error).__name__ if error is not None else None
        error_message = None
        if error is not None:
            error_message = (
                error.message if isinstance(error, AppError) else f"Unexpected error: {error}"
            )
        duration = self._recorder.finish(
            t.session_id,
            status,
            state,
            error_type,
            error_message,
            actual_dwell_seconds=t.actual_dwell,
            scroll_actions=t.scroll_actions,
        )
        result = SessionResult(
            session_id=t.session_id,
            iteration=iteration,
            status=status,
            state=state,
            target_found=t.target_found,
            target_position=t.target_position,
            planned_dwell_seconds=t.planned_dwell,
            actual_dwell_seconds=t.actual_dwell,
            duration_seconds=duration,
            error_type=error_type,
            error_message=error_message,
        )
        self._observer.on_session_finished(result)
        log.info(
            "session_finished",
            session_id=t.session_id,
            status=status.value,
            position=t.target_position,
            duration=duration,
        )
        return result
