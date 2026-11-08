"""Fixtures shared by the end-to-end tests.

A journey now begins with a real question to Google, so these tests answer it
with :class:`tests.helpers.fake_google.FakeGoogleServer` - a local double of the
Custom Search JSON API that speaks the documented response format. Everything
else is the real thing: the HTTP client, the ranking service that stores the
observation, the safety service, Chromium, the sample website and the database.

The scenarios here select ``GOOGLE_API`` on purpose. The other channel reads
google.com itself, which no test may do; its own rules are covered offline by
``tests/unit/test_google_search_page.py`` and ``tests/unit/test_search_page_actions.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.app.container import AppContainer
from src.domain.enums import Environment, SearchProfile
from src.domain.schemas import BrowserProfileInput, ScenarioInput
from tests.helpers.fake_google import VALID_CX, VALID_KEY, FakeGoogleServer, result_item

TARGET_SITE_NAME = "Sample site (as a listed site)"


def configure(container: AppContainer, **overrides: Any) -> int:
    """Change the demo scenario's settings; returns its id."""
    scenario = container.scenarios.search()[0]
    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(scenario.id, data | overrides)
    return int(scenario.id)


def catalogue(target_url: str, position: int | None, total: int = 10) -> list[dict[str, str]]:
    """What the double answers: ``target_url`` at ``position``, or nowhere at all."""
    items = [result_item(n, f"https://other{n}.example/page") for n in range(1, total + 1)]
    if position is not None:
        items[position - 1] = result_item(position, target_url, "Handmade ceramics")
    return items


@pytest.fixture
def listed_site(google_container: AppContainer) -> Any:
    """The sample website, registered as a site a search engine could list.

    The demo's own site is a ``LOCAL`` environment, which is exactly what tells the
    application Google can never index it; registering the same page as a controlled
    test site is what lets an end-to-end test walk the ranking path against a page
    it really can serve.
    """
    existing = next(
        (
            site
            for site in google_container.target_sites.search()
            if site.display_name == TARGET_SITE_NAME
        ),
        None,
    )
    if existing is not None:
        return existing
    return google_container.target_sites.create(
        {
            "display_name": TARGET_SITE_NAME,
            "base_url": f"{google_container.servers.target.url}/",
            "environment": Environment.CONTROLLED_TEST,
            "approved_for_automation": True,
        }
    )


@pytest.fixture
def app(
    google_container: AppContainer, fake_google: FakeGoogleServer, listed_site: Any
) -> Iterator[AppContainer]:
    """Container with a fast, headless journey that reads its ranking from the double."""
    container = google_container
    profile = container.browser_profiles.list()[0]
    values = {name: getattr(profile, name) for name in BrowserProfileInput.model_fields}
    container.browser_profiles.update(profile.id, values | {"headless": True})
    container.google.credentials.save(VALID_KEY, VALID_CX)
    fake_google.state.items = catalogue(f"{container.servers.target.url}/", position=4)
    configure(
        container,
        # The double stands in for the API, so the scenario asks the API. Reading a
        # real Google page is never part of a test run.
        search_profile=SearchProfile.GOOGLE_API,
        target_site_id=listed_site.id,
        min_dwell_seconds=1,
        max_dwell_seconds=2,
        iterations=1,
        max_scroll_actions=4,
    )
    yield container
