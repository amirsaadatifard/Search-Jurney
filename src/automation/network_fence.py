"""Network-level fence around the test browser.

Playwright's request interception does **not** see the hops of a redirect, so a
page could be redirected from an approved site to anywhere and the browser
would contact the destination before our checks run. The fence closes that gap
below the application: Chromium is started with a proxy that does not exist and
a bypass list naming only the approved origins. Everything else, including
redirect targets, other ports on this computer and raw IP addresses, fails with
``ERR_PROXY_CONNECTION_FAILED`` before any connection is attempted.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from src.services.safety_service import DEFAULT_PORTS, AllowedOrigin

# Nothing listens on the discard port; connecting to it is refused immediately.
UNREACHABLE_PROXY: Final = "127.0.0.1:9"
BLOCKED_ERROR: Final = "ERR_PROXY_CONNECTION_FAILED"


def bypass_entries(origins: Iterable[AllowedOrigin]) -> list[str]:
    """Bypass-list entries (``host:port``) for the given origins, sorted and unique."""
    entries: set[str] = set()
    for origin in origins:
        host = f"[{origin.host}]" if ":" in origin.host else origin.host
        ports = DEFAULT_PORTS.values() if origin.port is None else (origin.port,)
        entries.update(f"{host}:{port}" for port in ports)
    return sorted(entries)


def fence_arguments(origins: Iterable[AllowedOrigin]) -> list[str]:
    """Chromium command-line arguments that confine the browser to ``origins``.

    ``<-loopback>`` removes Chromium's implicit "never proxy localhost" rule so
    that other services on this computer are fenced off as well.
    """
    allowed = ";".join(["<-loopback>", *bypass_entries(origins)])
    return [f"--proxy-server=http://{UNREACHABLE_PROXY}", f"--proxy-bypass-list={allowed}"]
