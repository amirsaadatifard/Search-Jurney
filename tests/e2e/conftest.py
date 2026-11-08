"""Fixtures shared by the end-to-end tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.app.container import AppContainer
from src.domain.schemas import BrowserProfileInput, ScenarioInput


def configure(container: AppContainer, **overrides: Any) -> int:
    """Change the demo scenario's settings; returns its id."""
    scenario = container.scenarios.search()[0]
    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(scenario.id, data | overrides)
    return int(scenario.id)


@pytest.fixture
def app(container: AppContainer) -> Iterator[AppContainer]:
    """Container with a fast, headless demo scenario."""
    profile = container.browser_profiles.list()[0]
    values = {name: getattr(profile, name) for name in BrowserProfileInput.model_fields}
    container.browser_profiles.update(profile.id, values | {"headless": True})
    configure(
        container, min_dwell_seconds=1, max_dwell_seconds=2, iterations=1, max_scroll_actions=4
    )
    yield container
