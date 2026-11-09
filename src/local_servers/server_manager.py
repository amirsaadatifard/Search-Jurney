"""Runs the bundled FastAPI sample website inside the desktop process.

Each server is a ``uvicorn.Server`` on its own daemon thread with its own event
loop, bound to the loopback interface only. Shutdown is cooperative (uvicorn's
``should_exit`` flag); threads are never killed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Final

import uvicorn
from fastapi import FastAPI

from src.app.constants import LOOPBACK_HOST
from src.domain.exceptions import AppError
from src.local_servers.target_site.app import SERVICE_NAME as TARGET_SERVICE_NAME
from src.local_servers.target_site.app import create_target_app
from src.logging_config import get_logger

log = get_logger(__name__)

STARTUP_TIMEOUT_SECONDS: Final = 10.0
SHUTDOWN_TIMEOUT_SECONDS: Final = 5.0
_POLL_INTERVAL_SECONDS: Final = 0.02


class ServerStartError(AppError):
    """A local server could not be started (usually: the port is taken)."""


@dataclass(frozen=True, slots=True)
class ServerStatus:
    name: str
    url: str
    running: bool


class LocalServer:
    """One FastAPI application served on ``127.0.0.1:<port>`` in a background thread."""

    def __init__(self, name: str, app: FastAPI, port: int, host: str = LOOPBACK_HOST) -> None:
        self.name = name
        self.port = port
        self.host = host
        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return (
            self._server is not None
            and self._server.started
            and self._thread is not None
            and self._thread.is_alive()
        )

    def status(self) -> ServerStatus:
        return ServerStatus(self.name, self.url, self.is_running)

    def start(self) -> None:
        """Start serving and block until the socket accepts connections."""
        if self.is_running:
            return
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_config=None,  # keep the application's logging configuration
            access_log=False,
            lifespan="off",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=_serve, args=(server, self.name), name=f"server-{self.port}", daemon=True
        )
        thread.start()

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if not thread.is_alive():
                raise ServerStartError(
                    f"{self.name} could not start on {self.url}. "
                    "The port is probably in use by another program."
                )
            if time.monotonic() > deadline:
                server.should_exit = True
                raise ServerStartError(f"{self.name} did not start within the time limit.")
            time.sleep(_POLL_INTERVAL_SECONDS)

        self._server, self._thread = server, thread
        log.info("local_server_started", server=self.name, url=self.url)

    def stop(self) -> None:
        """Ask the server to finish open requests and wait for its thread."""
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is None or thread is None:
            return
        server.should_exit = True
        thread.join(SHUTDOWN_TIMEOUT_SECONDS)
        if thread.is_alive():
            log.warning("local_server_stop_timeout", server=self.name)
        else:
            log.info("local_server_stopped", server=self.name)


def _serve(server: uvicorn.Server, name: str) -> None:
    """Thread body. uvicorn calls ``sys.exit`` when it cannot bind its socket;
    that must end this thread quietly instead of surfacing as a crash."""
    try:
        server.run()
    except SystemExit:
        log.warning("local_server_exited_during_startup", server=name)


class LocalServerManager:
    """Owns the bundled sample website (a safe site to visit, never a search source).

    Search results do not come from a local server any more: they come from the
    real Google results page (:mod:`src.automation`), so the only server the
    application still needs is the sample site the demo journeys open.
    """

    def __init__(self, target_port: int) -> None:
        self.target = LocalServer(TARGET_SERVICE_NAME, create_target_app(), target_port)

    @property
    def servers(self) -> tuple[LocalServer, ...]:
        return (self.target,)

    @property
    def all_running(self) -> bool:
        return all(server.is_running for server in self.servers)

    def statuses(self) -> list[ServerStatus]:
        return [server.status() for server in self.servers]

    def is_serving_port(self, port: int) -> bool:
        """Whether one of *our* servers currently occupies ``port``."""
        return any(server.port == port and server.is_running for server in self.servers)

    def start_all(self) -> list[str]:
        """Start every bundled server. Returns error messages; an empty list means success."""
        errors: list[str] = []
        for server in self.servers:
            try:
                server.start()
            except ServerStartError as exc:
                log.error("local_server_start_failed", server=server.name, error=exc.message)
                errors.append(exc.message)
        return errors

    def stop_all(self) -> None:
        for server in self.servers:
            server.stop()
