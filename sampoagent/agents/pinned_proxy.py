"""Loopback HTTPS CONNECT proxy that pins DNS decisions to validated IPs."""

from __future__ import annotations

import base64
import hmac
import ipaddress
import select
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Any
from urllib.parse import urlsplit

from sampoagent.applications.urls import is_safe_public_https_url


Resolver = Callable[..., list[tuple[Any, ...]]]
Connector = Callable[..., socket.socket]


class UnsafeProxyDestination(ValueError):
    """Raised when a proxy destination is not provably public and permitted."""


def _global_addresses(host: str, port: int, resolver: Resolver) -> tuple[str, ...]:
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except (OSError, TypeError, ValueError) as exc:
        raise UnsafeProxyDestination("Destination DNS resolution failed") from exc
    if not answers:
        raise UnsafeProxyDestination("Destination did not resolve")

    addresses: list[str] = []
    for answer in answers:
        try:
            ip = ipaddress.ip_address(str(answer[4][0]))
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafeProxyDestination("Destination returned an invalid address") from exc
        if not ip.is_global:
            raise UnsafeProxyDestination("Destination resolved to a non-public address")
        address = ip.compressed
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def connect_to_validated_ip(
    host: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
    connector: Connector = socket.create_connection,
) -> socket.socket:
    """Resolve once, validate the full answer set, then dial numeric IPs only."""
    addresses = _global_addresses(host, port, resolver)
    failures: list[OSError] = []
    for address in addresses:
        try:
            return connector((address, port), timeout=10)
        except OSError as exc:
            failures.append(exc)
    if failures:
        raise OSError("Could not connect to any validated public address") from failures[-1]
    raise UnsafeProxyDestination("Destination did not provide a usable address")


def _normalize_host(host: str) -> str:
    try:
        return ipaddress.ip_address(host).compressed.casefold()
    except ValueError:
        try:
            return host.encode("idna").decode("ascii").casefold().rstrip(".")
        except UnicodeError as exc:
            raise UnsafeProxyDestination("Destination hostname is invalid") from exc


def _host_url(host: str, port: int) -> str:
    formatted = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"https://{formatted}:{port}/"


class _PinnedProxyServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], owner: "PinnedHttpsProxy") -> None:
        self.owner = owner
        super().__init__(address, _ConnectHandler)


class _ConnectHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SampoAgentProxy"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        # Request targets can contain opaque employer identifiers; never log them.
        return

    def _reject(self, status: int) -> None:
        self.send_response(status)
        if status == 407:
            self.send_header("Proxy-Authenticate", 'Basic realm="SampoAgent local proxy"')
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:
        self._reject(405)

    def do_POST(self) -> None:
        self._reject(405)

    def do_PUT(self) -> None:
        self._reject(405)

    def do_DELETE(self) -> None:
        self._reject(405)

    def do_CONNECT(self) -> None:
        owner: PinnedHttpsProxy = self.server.owner  # type: ignore[attr-defined]
        if not owner._authorized(self.headers.get("Proxy-Authorization", "")):
            self._reject(407)
            return
        try:
            host, port = _parse_connect_authority(self.path)
            if not owner._destination_allowed(host, port) or not is_safe_public_https_url(_host_url(host, port)):
                self._reject(403)
                return
        except (TypeError, ValueError, UnsafeProxyDestination):
            self._reject(400)
            return

        generation = owner._begin_tunnel(self.connection)
        if generation is None:
            try:
                self._reject(503)
            except OSError:
                pass
            return

        upstream: socket.socket | None = None
        try:
            addresses = _global_addresses(host, port, owner._resolver)
            upstream = owner._connect_if_current(self.connection, port, addresses, generation)
            if upstream is None:
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            self._tunnel(upstream)
        except (OSError, UnsafeProxyDestination):
            try:
                self._reject(502)
            except OSError:
                pass
        finally:
            owner._end_tunnel(self.connection, upstream)
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    def _tunnel(self, upstream: socket.socket) -> None:
        client = self.connection
        try:
            client.settimeout(None)
            upstream.settimeout(None)
        except OSError:
            return
        sockets = (client, upstream)
        while True:
            try:
                readable, _, exceptional = select.select(sockets, (), sockets, 120)
            except (OSError, ValueError):
                return
            if exceptional or not readable:
                return
            for source in readable:
                target = upstream if source is client else client
                try:
                    data = source.recv(65536)
                    if not data:
                        return
                    target.sendall(data)
                except OSError:
                    return


def _parse_connect_authority(authority: str) -> tuple[str, int]:
    """Parse the strict host:port authority form required by CONNECT."""
    if (
        not authority
        or any(character in authority for character in "/?#@\\%")
        or any(ord(character) <= 32 or ord(character) == 127 for character in authority)
    ):
        raise ValueError("Malformed CONNECT authority")
    target = urlsplit("//" + authority)
    if (
        target.netloc != authority or target.username is not None or target.password is not None
        or target.path or target.query or target.fragment
    ):
        raise ValueError("Malformed CONNECT authority")
    try:
        host = target.hostname or ""
        port = target.port
    except ValueError as exc:
        raise ValueError("Malformed CONNECT authority") from exc
    if not host or port is None or not 1 <= port <= 65535:
        raise ValueError("Malformed CONNECT authority")
    return host, port


class PinnedHttpsProxy:
    """Authenticated ephemeral loopback proxy for Playwright's HTTPS traffic."""

    def __init__(
        self,
        allowed_origin: str | None = None,
        *,
        resolver: Resolver = socket.getaddrinfo,
        connector: Connector = socket.create_connection,
    ) -> None:
        self._resolver = resolver
        self._connector = connector
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._origin: tuple[str, int] | None = None
        self._server: _PinnedProxyServer | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._closing = True
        self._active_tunnels: set[socket.socket] = set()
        self._username = "sampoagent"
        self._password = secrets.token_urlsafe(32)
        if allowed_origin is not None:
            self.set_allowed_origin(allowed_origin)

    @property
    def started(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("The local HTTPS proxy is not running")
        return int(self._server.server_address[1])

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def playwright_proxy(self) -> dict[str, str]:
        return {
            "server": self.proxy_url,
            "username": self._username,
            "password": self._password,
            # Chromium otherwise has implicit localhost/link-local bypasses.
            # Make the required proxy path explicit instead of relying on
            # Playwright's version-specific launch defaults.
            "bypass": "<-loopback>",
        }

    def set_allowed_origin(self, url: str | None) -> None:
        origin: tuple[str, int] | None = None
        if url is not None:
            if not is_safe_public_https_url(url):
                raise UnsafeProxyDestination("Only a safe public HTTPS origin can be allowed")
            parsed = urlsplit(url)
            if parsed.scheme.casefold() != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise UnsafeProxyDestination("Only a safe public HTTPS origin can be allowed")
            try:
                port = parsed.port or 443
            except ValueError as exc:
                raise UnsafeProxyDestination("The allowed origin has an invalid port") from exc
            origin = (_normalize_host(parsed.hostname), port)
        with self._lock:
            self._origin = origin

    def _destination_allowed(self, host: str, port: int) -> bool:
        try:
            normalized = _normalize_host(host)
        except UnsafeProxyDestination:
            return False
        with self._lock:
            origin = self._origin
        if origin is not None:
            return (normalized, port) == origin
        # The standalone/manual browser is broader, but never tunnels arbitrary ports.
        return port == 443

    def _authorized(self, header: str) -> bool:
        expected = "Basic " + base64.b64encode(f"{self._username}:{self._password}".encode()).decode("ascii")
        return hmac.compare_digest(header, expected)

    def _begin_tunnel(self, client: socket.socket) -> int | None:
        with self._lock:
            if self._closing or self._server is None:
                return None
            self._active_tunnels.add(client)
            return self._generation

    def _connect_if_current(
        self, client: socket.socket, port: int, addresses: tuple[str, ...], generation: int
    ) -> socket.socket | None:
        failures: list[OSError] = []
        # Serialize the bounded TCP connect phase with shutdown. DNS remains
        # outside this lock; a stale resolver result cannot start a new dial.
        with self._lock:
            if self._closing or self._generation != generation or client not in self._active_tunnels:
                return None
            for address in addresses:
                try:
                    upstream = self._connector((address, port), timeout=10)
                    self._active_tunnels.add(upstream)
                    return upstream
                except OSError as exc:
                    failures.append(exc)
        if failures:
            raise OSError("Could not connect to any validated public address") from failures[-1]
        raise UnsafeProxyDestination("Destination did not provide a usable address")

    def _end_tunnel(self, client: socket.socket, upstream: socket.socket | None) -> None:
        with self._lock:
            self._active_tunnels.discard(client)
            if upstream is not None:
                self._active_tunnels.discard(upstream)

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._server is not None:
                    return
            server = _PinnedProxyServer(("127.0.0.1", 0), self)
            thread = threading.Thread(target=server.serve_forever, name="sampoagent-pinned-proxy", daemon=True)
            try:
                thread.start()
            except Exception:
                server.server_close()
                raise
            with self._lock:
                self._generation += 1
                self._closing = False
                self._server = server
                self._thread = thread

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                server = self._server
                thread = self._thread
                self._server = None
                self._thread = None
                self._closing = True
                self._generation += 1
                active_tunnels = tuple(self._active_tunnels)
                self._active_tunnels.clear()
            for active_socket in active_tunnels:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    active_socket.close()
                except OSError:
                    pass
            if server is not None:
                server.shutdown()
                server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
