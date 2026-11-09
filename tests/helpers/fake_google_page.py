"""A local stand-in for Google's own pages, so a journey can be tested for real.

This double is deliberately *not* an API. It serves HTML shaped like the pages
Chromium is asked to browse - a start page with a search box, results as ``div.g``
blocks, a ``#pnnext`` link, a consent screen, the robot-check page - and a result
link goes through the same ``/url?q=`` redirect Google uses, so clicking one is a
real navigation through a real 302. A test can therefore prove the whole walk (open
the page, type the keyword, read the results, page forward, click through, dwell)
without asking Google anything: no key, no quota, no network.

The pages are plain HTTP on ``127.0.0.1``. The journey's entry check insists on
``https`` for the real engine, so a test that uses this double passes its own
scheme through :class:`~src.services.safety_service.SearchScope`; nothing about the
allowlist rules is loosened for it.
"""

from __future__ import annotations

import html
import threading
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

#: ``ok`` serves results; the others are the three ways Google declines to.
MODES: tuple[str, ...] = ("ok", "consent", "blocked", "empty")

RESULTS_PER_PAGE = 10

_RESULT_BLOCK = """
<div class="g">
  <h3>{title}</h3>
  <a href="{href}">{title}</a>
</div>
"""

_HOME_PAGE = """<!doctype html>
<html lang="en"><head><title>Search</title></head>
<body>
  <form action="/search" method="get">
    <textarea name="q" aria-label="Search"></textarea>
    <button type="submit">Search</button>
  </form>
  <script>
    // Google's own box submits on Enter although it is a textarea; a journey types
    // and presses Enter, so the double has to behave the same way.
    document.querySelector('textarea[name="q"]').addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        event.target.form.submit();
      }
    });
  </script>
</body></html>
"""

_CONSENT_PAGE = """<!doctype html>
<html lang="en"><head><title>Before you continue</title></head>
<body>
  <h1>Before you continue</h1>
  <button type="button" onclick="window.location='{back}'">Reject all</button>
</body></html>
"""

_BLOCKED_PAGE = """<!doctype html>
<html lang="en"><head><title>Confirm you're not a robot</title></head>
<body>
  <h1>unusual traffic</h1>
  <p>Our systems have detected unusual traffic from your computer network.</p>
</body></html>
"""


class FakeGooglePages:
    """Google-shaped pages, served by a background thread on ``127.0.0.1``."""

    def __init__(self, results: Sequence[tuple[str, str]] = ()) -> None:
        #: ``(title, url)`` in ranking order; page *n* shows ``results[n-1:n]``.
        self.results: list[tuple[str, str]] = []
        #: How many result pages exist. More results than one page holds means the
        #: journey sees a "next" control and has to walk to find the target.
        self.pages = 1
        self.set_results(results)
        self.mode: str = "ok"
        #: Whether the cookie wall has been answered; it stays answered after that.
        self.consent_answered = False
        #: Every request the browser made, in order - what a test asserts against.
        self.requests: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def set_results(self, results: Sequence[tuple[str, str]], *, pages: int = 0) -> None:
        """Replace the listing; the page count follows it unless a test says otherwise."""
        self.results = list(results)
        self.pages = max(1, pages, -(-len(self.results) // RESULTS_PER_PAGE))
        self.consent_answered = False

    # -- wiring ------------------------------------------------------------------ #
    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def scope(self) -> dict[str, Any]:
        """The ``SearchScope`` arguments that point a journey at this server."""
        return {
            "entry": f"{self.origin}/",
            "pages": ("/", "/search", "/url", "/redirect-away"),
            "assets": (self.origin,),
        }

    def listing(self, page: int) -> list[tuple[str, str]]:
        start = (page - 1) * RESULTS_PER_PAGE
        return self.results[start : start + RESULTS_PER_PAGE]

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- page building ----------------------------------------------------------- #
    def render_results(self, query: str, page: int) -> str:
        if self.mode == "blocked":
            return _BLOCKED_PAGE
        blocks = "".join(
            _RESULT_BLOCK.format(title=html.escape(title), href=html.escape(link))
            for title, link in self.listing(page)
        )
        if self.mode == "empty":
            blocks = "<div>nothing here</div>"
        next_link = ""
        if page < self.pages and self.mode in {"ok", "consent"}:
            start = page * RESULTS_PER_PAGE
            next_link = f'<a id="pnnext" href="/search?q={query}&start={start}">Next</a>'
        return (
            "<!doctype html><html lang='en'><head><title>"
            f"{html.escape(query)} - Search</title></head><body>"
            f"<div id='rso'>{blocks}</div><div id='nav'>{next_link}</div>"
            "</body></html>"
        )


def _make_handler(pages: FakeGooglePages) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # named by BaseHTTPRequestHandler, not chosen here
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            pages.requests.append(self.path)
            if parts.path in {"/sorry", "/sorry/index"}:
                self._send(_BLOCKED_PAGE, status=429)
                return
            if parts.path == "/redirect-away":
                # A result that leaves for a host nobody approved: what the click
                # through Google's own wrapper must never be allowed to become.
                self._redirect(query.get("to", ["https://nowhere.invalid/"])[0])
                return
            if parts.path == "/url":
                self._redirect(unquote(query.get("q", [""])[0]))
                return
            if parts.path == "/consent":
                # Answering the wall sends the browser back where it came from, and
                # the wall is then gone: the point of the test is that the journey
                # clicks "Reject all" instead of mistaking the page for a refusal.
                pages.consent_answered = True
                back = unquote(query.get("to", ["/"])[0])
                self._send(_CONSENT_PAGE.format(back=html.escape(back, quote=True)))
                return
            if pages.mode == "consent" and not pages.consent_answered:
                self._redirect(f"/consent?to={quote(self.path)}")
                return
            if parts.path == "/search":
                start = int(query.get("start", ["0"])[0] or 0)
                page = start // RESULTS_PER_PAGE + 1
                self._send(pages.render_results(query.get("q", [""])[0], page))
                return
            if parts.path in {"", "/"}:
                self._send(_HOME_PAGE)
                return
            self._send("<html><body>not found</body></html>", status=404)

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # pages.requests already holds what a test asserts against

    return Handler


def result_item(position: int, url: str, title: str = "") -> tuple[str, str]:
    """One ``(title, url)`` entry, at the position the listing puts it at."""
    return (title or f"Result number {position}", url)


def listing(
    target_url: str, *, positions: Sequence[int] = (4,), total: int = 10
) -> list[tuple[str, str]]:
    """A result list with ``target_url`` at ``positions`` and filler around it.

    ``positions`` are 1-based across the whole listing, so ``(13,)`` means the third
    result of the second page - the shape that forces a journey to page forward.
    """
    wanted = {position - 1 for position in positions}
    count = max(total, max(wanted, default=0) + 1)
    items = [
        (
            f"Handmade ceramics {index + 1}" if index in wanted else f"Somebody else {index + 1}",
            target_url if index in wanted else f"https://elsewhere{index}.example/page",
        )
        for index in range(count)
    ]
    return items
