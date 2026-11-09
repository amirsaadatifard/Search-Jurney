"""Playwright browser lifecycle and the request guard.

One browser is launched per run. Each session gets its own browser context;
with ``incognito`` off a single context is kept and only the requested state
(cookies, local storage) is cleared between sessions.

The guard is the enforcement point for the "never leave the approved domain"
rule: it inspects *every* request the page makes and aborts anything the
:class:`DomainSafetyService` does not allow.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.automation.models import BrowserSettings, SessionPlan
from src.automation.network_fence import fence_arguments
from src.domain.enums import UserAgentMode
from src.domain.exceptions import (
    AppError,
    AutomationTimeoutError,
    BrowserLaunchError,
    NavigationError,
    SafetyViolationError,
)
from src.logging_config import get_logger
from src.services.safety_service import AllowedOrigin, DomainSafetyService, normalize_url

log = get_logger(__name__)

TEST_AGENT_LABEL: Final = "SearchJourneyLab/0.1 (automated-test)"
MS_PER_SECOND: Final = 1000
_NETWORK_SCHEMES: Final = frozenset({"http", "https"})
_LAUNCH_HINT: Final = "Install the browser with: python -m playwright install chromium"


@dataclass(slots=True)
class BlockedRequest:
    url: str
    is_navigation: bool
    reason: str


@dataclass(slots=True)
class BrowserSession:
    """An open context and page together with what the guard has blocked so far."""

    context: BrowserContext
    page: Page
    blocked: list[BlockedRequest] = field(default_factory=list)
    # The search step sets ``search_scope_open`` around its own navigation and
    # supplies the predicate; no other request can borrow the exception, because
    # no other code path turns it on.
    search_scope_open: bool = False
    search_allowed: Callable[[str], bool] | None = None

    def navigation_violation(self) -> BlockedRequest | None:
        return next((item for item in self.blocked if item.is_navigation), None)

    def allows_search_origin(self, url: str) -> bool:
        """Whether the results page this session is reading may load ``url``."""
        if not self.search_scope_open or self.search_allowed is None:
            return False
        return self.search_allowed(url)


class BrowserManager:
    def __init__(self, safety: DomainSafetyService) -> None:
        self._safety = safety
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._shared: BrowserSession | None = None
        self._default_agent: str | None = None
        self._fence_active = False
        self.on_blocked: Any = None  # optional callable(BlockedRequest)

    @property
    def fence_active(self) -> bool:
        """Whether redirect hops and stray requests are blocked at the network level."""
        return self._fence_active

    @property
    def is_launched(self) -> bool:
        return self._browser is not None

    # -- lifecycle ---------------------------------------------------------------- #
    async def launch(self, settings: BrowserSettings, plan: SessionPlan) -> None:
        """Start Chromium for this run."""
        if self._browser is not None:
            return
        launch_args: dict[str, Any] = {"headless": settings.headless, "args": []}
        if plan.proxy is not None:
            proxy: dict[str, str] = {"server": plan.proxy.server}
            if plan.proxy.username:
                proxy["username"] = plan.proxy.username
            if plan.proxy.password:
                proxy["password"] = plan.proxy.password
            launch_args["proxy"] = proxy
            # Chromium sends loopback traffic directly unless told otherwise.
            launch_args["args"].append("--proxy-bypass-list=<-loopback>")
            self._fence_active = False  # one proxy setting cannot serve both purposes
        else:
            extra: list[AllowedOrigin] = []
            urls = [plan.target_url]
            if plan.searches_in_browser:
                # The journey browses Google's own pages, so the fence has to let them
                # through as well - otherwise Chromium cannot even load a results page
                # and every search journey dies with a proxy error. The list is the
                # same one the request guard narrows again per URL; it is added for
                # the whole run rather than per step because Chromium's arguments are
                # fixed at launch, and the guard is what keeps the scope honest.
                extra = list(self._safety.search_origins())
            origins = [AllowedOrigin.from_url(normalize_url(url)) for url in urls if url]
            origins.extend(extra)
            launch_args["args"].extend(fence_arguments(origins))
            self._fence_active = True
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(**launch_args)
        except PlaywrightError as exc:
            await self._stop_playwright()
            raise BrowserLaunchError(f"The browser could not be started. {_LAUNCH_HINT}") from exc
        log.info("browser_launched", version=self._browser.version, headless=settings.headless)

    async def open_session(self, plan: SessionPlan) -> BrowserSession:
        """Return a ready page. Reuses the shared context when ``incognito`` is off."""
        browser = self._require_browser()
        if not plan.incognito and self._shared is not None:
            self._shared.blocked.clear()
            return self._shared

        settings = plan.browser
        options: dict[str, Any] = {
            "viewport": {"width": settings.viewport_width, "height": settings.viewport_height},
            "java_script_enabled": settings.javascript_enabled,
            "accept_downloads": settings.downloads_enabled,
        }
        if settings.locale:
            options["locale"] = settings.locale
        if settings.timezone:
            options["timezone_id"] = settings.timezone
        if settings.user_agent_mode == UserAgentMode.LABELLED_TEST_AGENT.value:
            options["user_agent"] = f"{await self._browser_user_agent(browser)} {TEST_AGENT_LABEL}"

        context = await browser.new_context(**options)
        context.set_default_timeout(plan.timeout_seconds * MS_PER_SECOND)
        context.set_default_navigation_timeout(plan.timeout_seconds * MS_PER_SECOND)
        page = await context.new_page()
        session = BrowserSession(context, page, search_allowed=self._safety.is_search_origin)
        await context.route("**/*", lambda route, request: self._guard(session, route, request))
        page.on("request", lambda request: self._watch(session, request))
        if not plan.incognito:
            self._shared = session
        return session

    async def close_session(self, session: BrowserSession, plan: SessionPlan) -> None:
        """End a session; shared contexts survive but are cleaned as configured."""
        if not plan.incognito and session is self._shared:
            await self._clean_shared(session, plan)
            return
        await session.context.close()

    async def close(self) -> None:
        if self._shared is not None:
            await _quietly(self._shared.context.close())
            self._shared = None
        if self._browser is not None:
            await _quietly(self._browser.close())
            self._browser = None
        await self._stop_playwright()

    async def describe(self, session: BrowserSession, settings: BrowserSettings) -> dict[str, Any]:
        """Facts about the browser for the session record."""
        browser = self._require_browser()
        user_agent = await session.page.evaluate("navigator.userAgent")
        return {
            "browser": "chromium",
            "version": browser.version,
            "user_agent": user_agent,
            "headless": settings.headless,
            "viewport": f"{settings.viewport_width}x{settings.viewport_height}",
            "locale": settings.locale or "browser default",
            "timezone": settings.timezone or "browser default",
            "network_fence": self._fence_active,
        }

    # -- internals ---------------------------------------------------------------- #
    def _require_browser(self) -> Browser:
        if self._browser is None:
            raise BrowserLaunchError("The browser is not running.")
        return self._browser

    async def _guard(self, session: BrowserSession, route: Route, request: Request) -> None:
        """Allow a request only if its URL is inside the approved scope."""
        scheme = urlsplit(request.url).scheme
        if scheme not in _NETWORK_SCHEMES:
            await route.continue_()
            return
        if session.allows_search_origin(request.url):
            # Checked first on purpose: asking check_url() first would log a refusal
            # that is not one, and a debug console full of noise is worse than none.
            await route.continue_()
            return
        try:
            self._safety.check_url(request.url)
        except SafetyViolationError as exc:
            blocked = BlockedRequest(request.url, request.is_navigation_request(), exc.message)
            session.blocked.append(blocked)
            log.warning("request_blocked", url=request.url, navigation=blocked.is_navigation)
            if callable(self.on_blocked):
                self.on_blocked(blocked)
            await _quietly(route.abort("blockedbyclient"))
            return
        await _quietly(route.continue_())

    def _watch(self, session: BrowserSession, request: Request) -> None:
        """Record redirect hops that leave the approved scope.

        Route handlers are never called for redirects, so this event is the only
        place they can be noticed. With the network fence the hop fails right
        after; without it (proxy mode) this is detection only.
        """
        if request.redirected_from is None or urlsplit(request.url).scheme not in _NETWORK_SCHEMES:
            return
        if session.allows_search_origin(request.url):
            return  # a consent or paging hop inside the search scope is not a violation
        try:
            self._safety.check_url(request.url)
        except SafetyViolationError as exc:
            root = request
            while root.redirected_from is not None:
                root = root.redirected_from
            blocked = BlockedRequest(request.url, root.is_navigation_request(), exc.message)
            session.blocked.append(blocked)
            log.warning("redirect_hop_blocked", url=request.url, fence=self._fence_active)
            if callable(self.on_blocked):
                self.on_blocked(blocked)

    async def _clean_shared(self, session: BrowserSession, plan: SessionPlan) -> None:
        if plan.clear_cookies:
            await session.context.clear_cookies()
        if plan.clear_storage or plan.browser.clear_storage:
            await _quietly(
                session.page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            )

    async def _browser_user_agent(self, browser: Browser) -> str:
        if self._default_agent is None:
            context = await browser.new_context()
            try:
                page = await context.new_page()
                self._default_agent = str(await page.evaluate("navigator.userAgent"))
            finally:
                await context.close()
        return self._default_agent

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            await _quietly(self._playwright.stop())
            self._playwright = None


async def _quietly(awaitable: Any) -> None:
    """Await ``awaitable`` and ignore errors (closing things that already went away)."""
    with contextlib.suppress(PlaywrightError):
        await awaitable


def translate_error(exc: Exception, session: BrowserSession | None, action: str) -> AppError:
    """Turn a Playwright failure into the application's exception types.

    A request the guard blocked takes precedence: the real cause of the failed
    navigation is the safety rule, not the network.
    """
    if session is not None:
        violation = session.navigation_violation()
        if violation is not None:
            return SafetyViolationError(
                f"Navigation to '{violation.url}' was blocked: {violation.reason}",
                reason="redirect_blocked",
                url=violation.url,
            )
    if isinstance(exc, PlaywrightTimeoutError):
        return AutomationTimeoutError(f"Timed out while {action}.")
    return NavigationError(f"Failed while {action}: {_first_line(exc)}")


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
