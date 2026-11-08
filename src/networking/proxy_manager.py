"""Proxy connection test.

Connects to the proxy the user configured (and only to it) and checks that the
protocol answers: an HTTP response for HTTP/HTTPS proxies, the greeting - and
the username/password step when credentials are set - for SOCKS5. No traffic is
sent through the proxy to any third party; the HTTP probe asks for the proxy's
own loopback address.
"""

from __future__ import annotations

import base64
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Final

from src.domain.enums import ProxyProtocol, ProxyStatus

DEFAULT_TIMEOUT_SECONDS: Final = 5.0
_MS: Final = 1000
_HTTP_PREFIX: Final = b"HTTP/"
_AUTH_REQUIRED: Final = 407


@dataclass(frozen=True, slots=True)
class ProxyTestResult:
    status: ProxyStatus
    latency_ms: int | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.status is ProxyStatus.REACHABLE


class ProxyTester:
    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    def test(
        self,
        protocol: ProxyProtocol,
        host: str,
        port: int,
        username: str = "",
        password: str | None = None,
    ) -> ProxyTestResult:
        """Run the test. Never raises: failures become an ``UNREACHABLE`` result."""
        started = time.perf_counter()
        try:
            connection = socket.create_connection((host, port), timeout=self._timeout)
        except TimeoutError:
            return _failure(f"Timed out after {self._timeout:g} s.")
        except ConnectionRefusedError:
            return _failure("Connection refused: nothing is listening on that port.")
        except socket.gaierror:
            return _failure("The host name could not be resolved.")
        except OSError as exc:
            return _failure(f"Connection failed: {exc.strerror or exc}")
        latency = round((time.perf_counter() - started) * _MS)

        try:
            with connection:
                connection.settimeout(self._timeout)
                if protocol is ProxyProtocol.SOCKS5:
                    detail = self._probe_socks5(connection, username, password)
                else:
                    detail = self._probe_http(connection, protocol, host, username, password)
        except TimeoutError:
            return ProxyTestResult(ProxyStatus.UNREACHABLE, latency, _silent(protocol))
        except (OSError, ssl.SSLError) as exc:
            return ProxyTestResult(
                ProxyStatus.UNREACHABLE, latency, f"Probe failed: {_reason(exc)}"
            )
        if detail.startswith("!"):
            return ProxyTestResult(ProxyStatus.UNREACHABLE, latency, detail[1:])
        return ProxyTestResult(ProxyStatus.REACHABLE, latency, detail)

    # -- protocol probes ------------------------------------------------------------ #
    @staticmethod
    def _probe_http(
        connection: socket.socket,
        protocol: ProxyProtocol,
        host: str,
        username: str,
        password: str | None,
    ) -> str:
        stream: socket.socket | ssl.SSLSocket = connection
        if protocol is ProxyProtocol.HTTPS:
            stream = ssl.create_default_context().wrap_socket(connection, server_hostname=host)
        lines = ["HEAD http://127.0.0.1/ HTTP/1.1", "Host: 127.0.0.1", "Connection: close"]
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            lines.append(f"Proxy-Authorization: Basic {token}")
        stream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        reply = stream.recv(1024)
        if not reply.startswith(_HTTP_PREFIX):
            return "!The server answered, but it does not speak HTTP (wrong protocol?)."
        status = int(reply.split(b" ", 2)[1]) if reply.count(b" ") >= 2 else 0
        if status == _AUTH_REQUIRED:
            return (
                "!The proxy rejected the user name or password."
                if username
                else "Reachable, but it requires a user name and password."
            )
        return f"Reachable (HTTP {status})."

    @staticmethod
    def _probe_socks5(connection: socket.socket, username: str, password: str | None) -> str:
        methods = b"\x00\x02" if username else b"\x00"
        connection.sendall(b"\x05" + bytes([len(methods)]) + methods)
        reply = connection.recv(2)
        if len(reply) < 2 or reply[0] != 5:
            return "!The server answered, but it does not speak SOCKS5 (wrong protocol?)."
        method = reply[1]
        if method == 0xFF:
            if not username:
                return "!The proxy requires a user name and password."
            return "!The proxy accepts none of the offered authentication methods."
        if method == 0x02:
            if not username:
                return "!The proxy requires a user name and password."
            user, secret = username.encode(), (password or "").encode()
            connection.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
            outcome = connection.recv(2)
            if len(outcome) < 2 or outcome[1] != 0:
                return "!The proxy rejected the user name or password."
        return "Reachable (SOCKS5 handshake succeeded)."


def _failure(detail: str) -> ProxyTestResult:
    return ProxyTestResult(ProxyStatus.UNREACHABLE, None, detail)


def _silent(protocol: ProxyProtocol) -> str:
    """Message for a server that accepted the connection but never answered the probe."""
    expected = "a SOCKS5 greeting" if protocol is ProxyProtocol.SOCKS5 else "an HTTP request"
    other = "HTTP" if protocol is ProxyProtocol.SOCKS5 else "SOCKS5"
    return (
        f"Connected, but there was no answer to {expected}. "
        f"This port may be a {other} proxy or another kind of service."
    )


def _reason(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
