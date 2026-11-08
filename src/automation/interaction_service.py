"""Dwell timing and safe on-page interactions.

Pure scheduling (:func:`choose_dwell`, :func:`build_schedule`) is separate from
the page-level actions (:class:`InteractionService`) so the scheduling rules
can be tested exhaustively without a browser.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from src.automation.models import InteractionOutcome
from src.domain.enums import EventType
from src.domain.exceptions import SafetyViolationError
from src.services.safety_service import AllowedOrigin, DomainSafetyService, normalize_url

if TYPE_CHECKING:
    from playwright.async_api import Page

    from src.automation.control import RunControl

DWELL_RESOLUTION: Final = 1  # decimal places of a chosen dwell time
EARLIEST_ACTION_FRACTION: Final = 0.10
LATEST_ACTION_FRACTION: Final = 0.85
INTERNAL_VISIT_EVERY: Final = 4  # every n-th action visits another page of the site
SCROLL_STEPS: Final = 6
SCROLL_STEP_SECONDS: Final = 0.04
INTERNAL_PAGE_PAUSE_SECONDS: Final = 1.2
MIN_SCROLL_FRACTION: Final = 0.45
MAX_SCROLL_FRACTION: Final = 0.85


class ActionKind(StrEnum):
    SCROLL_DOWN = "scroll_down"
    SCROLL_UP = "scroll_up"
    VISIT_INTERNAL_PAGE = "visit_internal_page"


ACTION_LABELS: Final[dict[ActionKind, str]] = {
    ActionKind.SCROLL_DOWN: "Scrolling down",
    ActionKind.SCROLL_UP: "Scrolling up",
    ActionKind.VISIT_INTERNAL_PAGE: "Visiting another page of the site",
}


@dataclass(frozen=True, slots=True)
class ScheduledAction:
    """An interaction to perform once ``at_seconds`` of dwell time have elapsed."""

    at_seconds: float
    kind: ActionKind


def choose_dwell(minimum: int, maximum: int, rng: random.Random) -> float:
    """Pick a dwell time in ``[minimum, maximum]`` seconds."""
    if minimum > maximum:
        raise ValueError("minimum dwell time exceeds the maximum")
    return round(rng.uniform(minimum, maximum), DWELL_RESOLUTION)


def build_schedule(
    dwell_seconds: float, max_actions: int, rng: random.Random
) -> list[ScheduledAction]:
    """Plan the interactions for a dwell of ``dwell_seconds``.

    ``max_actions`` is an upper bound; between half of it (at least one) and
    all of it are scheduled. Actions are spread over the middle of the dwell so
    the page is neither acted on instantly nor left at the last moment. Mostly
    downward scrolling, an occasional scroll back up, and every fourth action a
    visit to another page of the same site.
    """
    if max_actions <= 0 or dwell_seconds <= 0:
        return []
    count = rng.randint(max(1, max_actions // 2), max_actions)
    start = dwell_seconds * EARLIEST_ACTION_FRACTION
    end = dwell_seconds * LATEST_ACTION_FRACTION
    span = max(end - start, 0.0)

    schedule: list[ScheduledAction] = []
    for index in range(count):
        slot = span * (index + 1) / (count + 1)
        jitter = (rng.random() - 0.5) * span / (count + 1) * 0.5
        at = round(min(max(start + slot + jitter, start), end), 2)
        if (index + 1) % INTERNAL_VISIT_EVERY == 0:
            kind = ActionKind.VISIT_INTERNAL_PAGE
        elif index > 0 and rng.random() < 0.25:
            kind = ActionKind.SCROLL_UP
        else:
            kind = ActionKind.SCROLL_DOWN
        schedule.append(ScheduledAction(at, kind))
    schedule.sort(key=lambda action: action.at_seconds)
    return schedule


class InteractionService:
    """Performs scheduled interactions on a Playwright page.

    Every interaction stays on the target's origin: internal links are filtered
    by origin before use, and navigation is additionally guarded by the
    browser-level request filter.
    """

    def __init__(self, safety: DomainSafetyService, control: RunControl) -> None:
        self._safety = safety
        self._control = control

    async def perform(
        self, page: Page, action: ScheduledAction, rng: random.Random
    ) -> InteractionOutcome:
        match action.kind:
            case ActionKind.SCROLL_DOWN:
                return await self._scroll(page, rng, direction=1)
            case ActionKind.SCROLL_UP:
                return await self._scroll(page, rng, direction=-1)
            case ActionKind.VISIT_INTERNAL_PAGE:
                return await self._visit_internal_page(page, rng)

    async def _scroll(self, page: Page, rng: random.Random, direction: int) -> InteractionOutcome:
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        distance = viewport["height"] * rng.uniform(MIN_SCROLL_FRACTION, MAX_SCROLL_FRACTION)
        for _ in range(SCROLL_STEPS):
            await page.mouse.wheel(0, direction * distance / SCROLL_STEPS)
            await self._control.sleep(SCROLL_STEP_SECONDS)
        position = await page.evaluate("Math.round(window.scrollY)")
        label = "down" if direction > 0 else "up"
        return InteractionOutcome(
            EventType.SCROLL.value,
            f"scrolled {label} to y={position}",
            {"direction": label, "y": position},
        )

    async def _visit_internal_page(self, page: Page, rng: random.Random) -> InteractionOutcome:
        """Open another page of the same site, look at it briefly, then go back."""
        origin = AllowedOrigin.from_url(normalize_url(page.url))
        hrefs: list[str] = await page.eval_on_selector_all(
            "a[href]", "links => links.map(link => link.href)"
        )
        candidates = sorted({href for href in hrefs if self._is_internal(href, origin, page.url)})
        if not candidates:
            return await self._scroll(page, rng, direction=1)

        destination = rng.choice(candidates)
        origin_url = page.url
        await page.goto(destination, wait_until="domcontentloaded")
        self._safety.check_redirect(origin_url, page.url)
        await self._control.sleep(INTERNAL_PAGE_PAUSE_SECONDS)
        visited = page.url
        await page.go_back(wait_until="domcontentloaded")
        self._safety.check_redirect(visited, page.url)
        return InteractionOutcome(
            EventType.PAGE_NAVIGATION.value,
            f"visited {visited} and returned",
            {"visited": visited, "returned_to": page.url},
        )

    def _is_internal(self, href: str, origin: AllowedOrigin, current_url: str) -> bool:
        try:
            url = normalize_url(href)
        except SafetyViolationError:
            return False
        if not origin.matches(url) or url.url == normalize_url(current_url).url:
            return False
        return self._safety.is_allowed(href)
