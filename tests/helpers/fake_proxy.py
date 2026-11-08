"""A tiny forward proxy for tests (HTTP proxy and SOCKS5 greeting), loopback only.

It only forwards requests whose destination is on 127.0.0.1, so a test can never
reach anything outside this computer through it.
"""

from __future__ import annotations

import base64
import http.client
import socket
import socketserver
import threading
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


class _Handler(socketserver.StreamRequestHandler):
    server: FakeHttpProxy  # type: ignore[assignment]

    def handle(self) -> None:
        request_line = self.rfile.readline(65536).decode("latin-1").strip()
        if not request_line:
            return
        headers: dict[str, str] = {}
        while True:
            line = self.rfile.readline(65536).decode("latin-1").strip()
            if not line:
                break
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        self.server.requests.append(request_line)

        if self.server.credentials is not None:
            expected = "Basic " + base64.b64encode(self.server.credentials.encode()).decode()
            if headers.get("proxy-authorization") != expected:
                self._send(
                    407,
                    b"proxy authentication required",
                    {"Proxy-Authenticate": 'Basic realm="test"'},
                )
                return

        method, _, rest = request_line.partition(" ")
        target = rest.rsplit(" ", 1)[0]
        parts = urlsplit(target)
        if method == "CONNECT" or parts.hostname not in ALLOWED_HOSTS:
            self.server.refused.append(target)
            self._send(403, b"destination not allowed by the test proxy")
            return
        try:
            upstream = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=10)
            upstream.request(
                method, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            )
            response = upstream.getresponse()
            body = response.read()
            forwarded = {
                k: v
                for k, v in response.getheaders()
                if k.lower() not in {"transfer-encoding", "connection", "content-length"}
            }
            self._send(response.status, body, forwarded)
        except OSError:
            self._send(502, b"bad gateway")

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", "Connection: close"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        self.wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


class FakeHttpProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, credentials: str | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.credentials = credentials  # "user:password" or None
        self.requests: list[str] = []
        self.refused: list[str] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class FakeSocksServer(socketserver.ThreadingTCPServer):
    """Answers the SOCKS5 greeting (and optional username/password sub-negotiation)."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, credentials: tuple[str, str] | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _SocksHandler)
        self.credentials = credentials
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _SocksHandler(socketserver.BaseRequestHandler):
    server: FakeSocksServer  # type: ignore[assignment]

    def handle(self) -> None:
        sock: socket.socket = self.request
        header = sock.recv(2)
        if len(header) < 2 or header[0] != 5:
            return
        methods = sock.recv(header[1])
        if self.server.credentials is None:
            sock.sendall(b"\x05\x00" if 0 in methods else b"\x05\xff")
            return
        if 2 not in methods:
            sock.sendall(b"\x05\xff")
            return
        sock.sendall(b"\x05\x02")
        version = sock.recv(1)
        user = sock.recv(sock.recv(1)[0]).decode()
        password = sock.recv(sock.recv(1)[0]).decode()
        ok = version == b"\x01" and (user, password) == self.server.credentials
        sock.sendall(b"\x01\x00" if ok else b"\x01\x01")
