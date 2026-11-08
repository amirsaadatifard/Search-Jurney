"""A local TEST DOUBLE of Google's Custom Search JSON API (loopback only).

It reproduces the *documented response shapes* (``items``, ``error.errors[].reason``)
so the real client code can be exercised without credentials or internet.
It exists only in the tests; the application has no such fallback.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

VALID_KEY = "test-api-key-123456"
VALID_CX = "test-engine-id"
PAGE = 10


@dataclass
class FakeGoogleState:
    """What the double answers. Tests mutate this between calls."""

    items: list[dict[str, str]] = field(default_factory=list)
    fail: Callable[[], tuple[int, dict[str, Any]]] | None = None
    requests: list[dict[str, str]] = field(default_factory=list)


def result_item(position: int, url: str, title: str = "", snippet: str = "") -> dict[str, str]:
    host = url.split("/")[2]
    return {
        "kind": "customsearch#result",
        "title": title or f"Result {position} - {host}",
        "link": url,
        "displayLink": host,
        "snippet": snippet or f"Snippet for result {position}.",
    }


def error_body(code: int, message: str, reason: str, status: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "errors": [{"message": message, "domain": "global", "reason": reason}],
            "status": status,
        }
    }


def create_app(state: FakeGoogleState) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/customsearch/v1")
    def search(request: Request) -> JSONResponse:
        params = dict(request.query_params)
        state.requests.append(params)
        if state.fail is not None:
            status, body = state.fail()
            return JSONResponse(body, status_code=status)
        if params.get("key") != VALID_KEY:
            return JSONResponse(
                error_body(
                    400,
                    "API key not valid. Please pass a valid API key.",
                    "keyInvalid",
                    "INVALID_ARGUMENT",
                ),
                status_code=400,
            )
        if params.get("cx") != VALID_CX:
            return JSONResponse(
                error_body(400, "Invalid Value", "invalid", "INVALID_ARGUMENT"), status_code=400
            )
        start = int(params.get("start", "1"))
        num = min(int(params.get("num", "10")), PAGE)
        page = state.items[start - 1 : start - 1 + num]
        body: dict[str, Any] = {
            "kind": "customsearch#search",
            "searchInformation": {"totalResults": str(len(state.items)), "searchTime": 0.1},
        }
        if page:
            body["items"] = page
        return JSONResponse(body)

    return app


class FakeGoogleServer:
    """Runs the double on a free loopback port in a background thread."""

    def __init__(self, items: list[dict[str, str]] | None = None) -> None:
        self.state = FakeGoogleState(items=items or [])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        config = uvicorn.Config(
            create_app(self.state),
            host="127.0.0.1",
            port=self.port,
            log_config=None,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        while not self._server.started:
            threading.Event().wait(0.02)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/customsearch/v1"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(5)
