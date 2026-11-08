"""FastAPI application of the local search-engine simulator.

Pages:
    ``/``            search form
    ``/search?q=``   results page (ten simulated results)
    ``/admin``       development page to inspect/change the target and its rank

JSON API (used by the automation runner and by tests):
    ``GET /api/search?q=``   the same results as data
    ``GET /api/config``      current configuration
    ``PUT /api/config``      replace the configuration
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from src.domain.exceptions import SafetyViolationError
from src.local_servers.common import create_base_app, create_template_environment
from src.local_servers.search_simulator import selectors
from src.local_servers.search_simulator.dataset import (
    MAX_QUERY_LENGTH,
    RESULTS_PER_PAGE,
    generate_results,
    normalise_query,
)
from src.local_servers.search_simulator.state import SimulatorConfig, SimulatorState

SERVICE_NAME = "Search Simulator"
BRAND_NAME = "SearchLab"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

UrlValidator = Callable[[str], object]


def create_search_app(state: SimulatorState, url_validator: UrlValidator | None = None) -> FastAPI:
    """Build the simulator.

    Args:
        state: Shared, mutable configuration (target URL and rank).
        url_validator: Called with a proposed target URL; must raise
            :class:`SafetyViolationError` to reject it. The application passes
            the domain-safety check here, so the simulator can only ever link
            to an approved target site.
    """
    app = create_base_app(SERVICE_NAME)
    templates = create_template_environment(_TEMPLATES_DIR)
    context: dict[str, Any] = {"brand": BRAND_NAME, "sel": selectors, "max_query": MAX_QUERY_LENGTH}

    def render(template: str, **values: Any) -> HTMLResponse:
        return HTMLResponse(templates.get_template(template).render(**context, **values))

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return render("index.html", query="")

    @app.get(selectors.SEARCH_PATH, response_model=None)
    def search(
        q: str = Query(default="", max_length=2000),
    ) -> HTMLResponse | RedirectResponse:
        query = normalise_query(q)
        if not query:
            return RedirectResponse("/", status_code=303)
        results = generate_results(query, state.get().target_result())
        return render("results.html", query=query, results=results)

    @app.get(selectors.SEARCH_API_PATH)
    def search_api(q: str = Query(default="", max_length=2000)) -> dict[str, Any]:
        query = normalise_query(q)
        results = generate_results(query, state.get().target_result()) if query else []
        return {"query": query, "count": len(results), "results": [asdict(r) for r in results]}

    @app.get(selectors.CONFIG_API_PATH)
    def get_config() -> SimulatorConfig:
        return state.get()

    @app.put(selectors.CONFIG_API_PATH)
    def put_config(config: SimulatorConfig) -> SimulatorConfig:
        if url_validator is not None:
            try:
                url_validator(config.target_url)
            except SafetyViolationError as exc:
                raise HTTPException(status_code=422, detail=exc.message) from exc
        state.set(config)
        return config

    @app.get("/admin", response_class=HTMLResponse)
    def admin() -> HTMLResponse:
        return render("admin.html", config=state.get(), positions=range(1, RESULTS_PER_PAGE + 1))

    return app
