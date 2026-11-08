"""Runtime configuration of the search simulator."""

from __future__ import annotations

import threading
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.constants import MAX_RESULT_POSITION
from src.local_servers.search_simulator.dataset import SearchResult

DEFAULT_TARGET_TITLE = "Kiln & Clay Studio - Handmade Ceramics"
DEFAULT_TARGET_DESCRIPTION = (
    "Small-batch stoneware made by hand: mugs, bowls and planters, plus weekend "
    "workshops for beginners. Local demo site for automation testing."
)


class SimulatorConfig(BaseModel):
    """Which target the simulator lists, and at which rank.

    ``position`` is 1-10; ``None`` leaves the target out of the results so the
    *target not found* path can be tested.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    target_url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    target_title: Annotated[str, StringConstraints(min_length=1, max_length=120)] = (
        DEFAULT_TARGET_TITLE
    )
    target_description: Annotated[str, StringConstraints(max_length=300)] = (
        DEFAULT_TARGET_DESCRIPTION
    )
    position: int | None = Field(default=3, ge=1, le=MAX_RESULT_POSITION)

    def target_result(self) -> SearchResult | None:
        """The target as a result entry, or ``None`` when it is not listed."""
        if self.position is None:
            return None
        return SearchResult(
            position=self.position,
            title=self.target_title,
            url=self.target_url,
            description=self.target_description,
            is_target=True,
        )


class SimulatorState:
    """Thread-safe holder of the current :class:`SimulatorConfig`.

    The GUI thread, the automation worker and the server thread all touch it.
    """

    def __init__(self, config: SimulatorConfig) -> None:
        self._config = config
        self._guard = threading.Lock()

    def get(self) -> SimulatorConfig:
        with self._guard:
            return self._config

    def set(self, config: SimulatorConfig) -> None:
        with self._guard:
            self._config = config
