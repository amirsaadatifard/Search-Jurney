"""Provider interface and the data it returns.

A provider only *reads* search data. It never opens a browser, never clicks a
result and never invents data: if the real service cannot answer, it raises one
of the ``Google*Error`` exceptions and the caller shows that error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """One organic result as returned by the provider."""

    position: int
    title: str
    url: str
    domain: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class ProviderCheck:
    """Outcome of a connectivity / credentials check."""

    ok: bool
    detail: str
    latency_ms: int | None = None


class SearchProvider(Protocol):
    """Anything that can return real search results for a query."""

    name: str

    def search(
        self, query: str, language: str = "", country: str = "", count: int = 10
    ) -> list[ProviderResult]:
        """Return up to ``count`` results, best first, positions starting at 1."""
        ...

    def check(self) -> ProviderCheck:
        """Verify credentials and reachability (may use one unit of quota)."""
        ...
