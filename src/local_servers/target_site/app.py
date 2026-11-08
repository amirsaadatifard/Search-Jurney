"""FastAPI application of the sample target website.

A small multi-page site with long, scrollable content. It is the default
approved target, so browser automation can be demonstrated without touching
anything outside this computer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from src.local_servers.common import create_base_app, create_template_environment
from src.local_servers.target_site import content

SERVICE_NAME: Final = "Sample Target Site"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Reserved, unresolvable address (RFC 2606) used by the redirect test fixture.
EXTERNAL_TEST_URL: Final = "https://outside-the-allowlist.example/"
EXTERNAL_REDIRECT_PATH: Final = "/test/redirect-external"

PAGE_PATHS: Final[tuple[str, ...]] = tuple(link.path for link in content.NAVIGATION)


def create_target_app() -> FastAPI:
    """Build the sample website."""
    app = create_base_app(SERVICE_NAME)
    templates = create_template_environment(_TEMPLATES_DIR)
    shared: dict[str, Any] = {"site_name": content.SITE_NAME, "navigation": content.NAVIGATION}

    def render(template: str, active: str, **values: Any) -> HTMLResponse:
        page = templates.get_template(template).render(**shared, active=active, **values)
        return HTMLResponse(page)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        return render(
            "home.html",
            "/",
            features=content.FEATURES,
            featured=content.PRODUCTS[:4],
            testimonials=content.TESTIMONIALS,
        )

    @app.get("/products", response_class=HTMLResponse)
    def products() -> HTMLResponse:
        return render("products.html", "/products", products=content.PRODUCTS)

    @app.get("/article", response_class=HTMLResponse)
    def article() -> HTMLResponse:
        return render(
            "article.html",
            "/article",
            title=content.ARTICLE_TITLE,
            sections=content.ARTICLE_SECTIONS,
        )

    @app.get("/about", response_class=HTMLResponse)
    def about() -> HTMLResponse:
        return render("about.html", "/about", team=content.TEAM, timeline=content.TIMELINE)

    @app.get("/contact", response_class=HTMLResponse)
    def contact() -> HTMLResponse:
        return render("contact.html", "/contact", hours=content.OPENING_HOURS)

    @app.get(EXTERNAL_REDIRECT_PATH)
    def redirect_external() -> RedirectResponse:
        """Test fixture: redirects off-site so the safety guard can be verified."""
        return RedirectResponse(EXTERNAL_TEST_URL, status_code=302)

    return app
