"""Helpers shared by the bundled FastAPI applications."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Sent with every response. These are local test pages: never cache, never index,
# never leak a referrer, and do not allow framing by other origins.
SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}


def create_template_environment(templates_dir: Path) -> Environment:
    """Jinja environment with HTML auto-escaping (user input is echoed in pages)."""
    return Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def create_base_app(title: str) -> FastAPI:
    """FastAPI app without the interactive docs and with the security headers applied."""
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": title}

    return app
