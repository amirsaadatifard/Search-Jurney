"""Fixtures shared by the end-to-end tests.

A journey now *browses*: it opens a search page in Chromium, types the keyword
into the box on that page, reads the results, walks forward when the target is not
on the first page, and clicks the target's own result. The page it browses here is
:class:`tests.helpers.fake_google_page.FakeGooglePages` - a local double that
serves Google-shaped HTML - so a whole journey runs offline, with no key, no quota
and nothing asked of Google itself.

Everything else is the real thing: Playwright and Chromium, the sample website the
journey ends up on, the safety service, the state machine and the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.app.container import AppContainer
from src.automation.google_search_page import set_pacing_gap
from src.domain.enums import Environment
from src.domain.schemas import BrowserProfileInput, ScenarioInput
from tests.helpers.fake_google_page import FakeGooglePages, listing

TARGET_SITE_NAME = "Sample site (as a listed site)"


@pytest.fixture(autouse=True)
def _no_pacing() -> Iterator[None]:
    """Skip the courtesy pause between reads: a test page cannot be rude to.

    Production keeps the two-second gap; only the pause is removed, never the
    single-request-per-page rule the gap enforces.
    """
    set_pacing_gap(0.0)
    yield
    set_pacing_gap(None)


def configure(container: AppContainer, **overrides: Any) -> int:
    """Change the demo scenario's settings; returns its id."""
    scenario = container.scenarios.search()[0]
    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(scenario.id, data | overrides)
    return int(scenario.id)


def catalogue(target_url: str, position: int | None, total: int = 10) -> list[tuple[str, str]]:
    """The listing the double serves: ``target_url`` at ``position``, or nowhere.

    Positions run across the pages, so ``13`` puts the site third on the second
    page of results and forces a journey to walk forward to find it.
    """
    positions = () if position is None else (position,)
    return listing(target_url, positions=positions, total=total)


@pytest.fixture
def listed_site(search_container: AppContainer) -> Any:
    """The sample website, registered as a site a search engine could list.

    The demo's own site is a ``LOCAL`` environment, which is what tells the
    application Google can never index it and that no search step is needed.
    Registering the same page as a controlled test site is what lets a journey walk
    the search path against a page this machine really can serve.
    """
    container = search_container
    existing = next(
        (site for site in container.target_sites.search() if site.display_name == TARGET_SITE_NAME),
        None,
    )
    if existing is not None:
        return existing
    return container.target_sites.create(
        {
            "display_name": TARGET_SITE_NAME,
            "base_url": f"{container.servers.target.url}/",
            "environment": Environment.CONTROLLED_TEST,
            "approved_for_automation": True,
        }
    )


@pytest.fixture
def app(
    search_container: AppContainer, fake_google: FakeGooglePages, listed_site: Any
) -> AppContainer:
    """A container whose journey browses the double, headless and quickly."""
    container = search_container
    profile = container.browser_profiles.list()[0]
    values = {name: getattr(profile, name) for name in BrowserProfileInput.model_fields}
    container.browser_profiles.update(profile.id, values | {"headless": True})
    fake_google.set_results(catalogue(f"{container.servers.target.url}/", position=4))
    configure(
        container,
        target_site_id=listed_site.id,
        min_dwell_seconds=1,
        max_dwell_seconds=2,
        iterations=1,
        max_scroll_actions=4,
    )
    return container
