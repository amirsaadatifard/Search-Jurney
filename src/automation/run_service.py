"""Runs scenarios on a background thread and exposes pause / resume / stop.

The GUI calls :meth:`start` and returns immediately; everything slow (the
search-API query, the browser, dwelling) happens on a worker thread with its own
asyncio loop, so the interface never freezes. Only one run may be active (the
configured maximum of concurrent sessions is 1); the design keeps run state in
this object so a pool could replace it later.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from src.app.paths import AppPaths
from src.automation.control import RunControl
from src.automation.models import MODE_GOOGLE, RunSummary, SessionPlan
from src.automation.observer import RunObserver, SafeObserver
from src.automation.plan_factory import PlanFactory
from src.automation.playwright_engine import PlaywrightEngine
from src.automation.rank_observer import RankObserver
from src.automation.recorder import SessionRecorder
from src.automation.screenshot_service import ScreenshotService
from src.automation.session_runner import SessionRunner
from src.database.session import Database
from src.domain.exceptions import AutomationError, InvalidConfigurationError
from src.local_servers.server_manager import LocalServerManager
from src.logging_config import get_logger
from src.networking.credentials import CredentialStore
from src.services.safety_service import DomainSafetyService
from src.services.scenario_service import ScenarioService
from src.services.settings_service import SettingsService

log = get_logger(__name__)

JOIN_TIMEOUT_SECONDS: Final = 15.0


class AutomationRunService:
    def __init__(
        self,
        database: Database,
        safety: DomainSafetyService,
        paths: AppPaths,
        settings: SettingsService,
        servers: LocalServerManager,
        scenarios: ScenarioService,
        credentials: CredentialStore,
        rank_observer: RankObserver | None = None,
    ) -> None:
        self._safety = safety
        self._paths = paths
        self._servers = servers
        self._scenarios = scenarios
        self._recorder = SessionRecorder(database)
        self._rank_observer = rank_observer
        self._plans = PlanFactory(settings, credentials)
        self._thread: threading.Thread | None = None
        self._control = RunControl()
        self._guard = threading.Lock()

    # -- state ---------------------------------------------------------------------- #
    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self.is_running and self._control.is_paused

    # -- commands (GUI thread) ----------------------------------------------------------- #
    def build_plans(
        self, scenario_ids: Sequence[int], iterations_override: int | None = None
    ) -> list[SessionPlan]:
        plans = [self._plans.from_scenario(self._scenarios.get(i)) for i in scenario_ids]
        if iterations_override is not None:
            plans = [replace(plan, iterations=iterations_override) for plan in plans]
        if not plans:
            raise InvalidConfigurationError("Select at least one scenario to run.")
        return plans

    def start(
        self,
        scenario_ids: Sequence[int],
        observer: RunObserver | None = None,
        iterations_override: int | None = None,
    ) -> None:
        """Start a run in the background. Raises if one is already active."""
        self.start_plans(self.build_plans(scenario_ids, iterations_override), observer)

    def _needs_sample_site(self, plan: SessionPlan) -> bool:
        """Whether a journey loads the bundled sample website (the only local server).

        Decided by where the visit goes, not by how the ranking was obtained: a
        site registered on this machine - the demo, or a development server the
        user approved under another environment - has to be serving before the
        browser is pointed at it. A journey against a real domain needs no local
        server at all and must not be the reason a port gets bound.
        """
        return plan.target_url.startswith(f"{self._servers.target.url}/")

    def _check_search_source(self, plans: Sequence[SessionPlan]) -> None:
        """Refuse a run whose journeys need real search data the app cannot obtain.

        Failing here costs nothing; failing inside the first iteration leaves a
        half-recorded session and a much less helpful message.
        """
        if not any(plan.mode == MODE_GOOGLE for plan in plans):
            return
        if self._rank_observer is not None and self._rank_observer.credentials_ready:
            return
        raise InvalidConfigurationError(
            "These journeys read the real Google ranking first, which needs an API key and a "
            "search engine id. Enter them under Google Research (or set SJAT_GOOGLE_API_KEY and "
            "SJAT_GOOGLE_ENGINE_ID) and start the run again."
        )

    def start_plans(self, plans: list[SessionPlan], observer: RunObserver | None = None) -> None:
        """Start a run from ready-made plans (used by Google Research mode)."""
        with self._guard:
            if self.is_running:
                raise AutomationError("A run is already in progress.")
            self._check_search_source(plans)
            if any(self._needs_sample_site(plan) for plan in plans):
                errors = self._servers.start_all()
                if errors:
                    raise AutomationError(" ".join(errors))
            self._control = RunControl()
            self._thread = threading.Thread(
                target=self._thread_main,
                args=(plans, observer, self._control),
                name="automation-run",
                daemon=True,
            )
            self._thread.start()

    def pause(self) -> None:
        self._control.pause()

    def resume(self) -> None:
        self._control.resume()

    def stop(self) -> None:
        self._control.stop()

    def join(self, timeout: float | None = JOIN_TIMEOUT_SECONDS) -> bool:
        """Wait for the run to end. Returns ``True`` if nothing is running any more."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.is_running

    def shutdown(self) -> None:
        """Stop a running run gracefully (application exit)."""
        if self.is_running:
            self._control.stop()
            if not self.join():
                log.warning("run_did_not_stop_in_time")

    # -- worker thread --------------------------------------------------------------------- #
    def _thread_main(
        self, plans: list[SessionPlan], observer: RunObserver | None, control: RunControl
    ) -> None:
        safe = SafeObserver(observer)
        summary = RunSummary()
        try:
            summary = asyncio.run(self._run_all(plans, safe, control))
        except Exception:
            log.exception("run_crashed")
        finally:
            safe.on_run_finished(summary)

    async def _run_all(
        self, plans: list[SessionPlan], observer: RunObserver, control: RunControl
    ) -> RunSummary:
        run_id = str(uuid.uuid4())
        total = RunSummary()
        observer.on_run_started(plans)
        for index, plan in enumerate(plans, start=1):
            if control.is_stopped:
                break
            observer.on_scenario_started(plan, index, len(plans))
            engine = PlaywrightEngine(
                self._safety,
                control,
                ScreenshotService(self._paths, self._recorder, plan.screenshots),
            )
            runner = SessionRunner(
                engine,
                self._recorder,
                self._safety,
                control,
                observer,
                rank_observer=self._rank_observer,
            )
            part = await runner.run_plan(plan, run_id)
            total.completed += part.completed
            total.target_not_found += part.target_not_found
            total.failed += part.failed
            total.stopped += part.stopped
        return total
