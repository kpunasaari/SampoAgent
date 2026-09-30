import base64
import logging
import socket
import threading

import pytest

from sampoagent.agents.pinned_proxy import (
    PinnedHttpsProxy,
    UnsafeProxyDestination,
    connect_to_validated_ip,
)


def _answer(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr


def _connect_request(proxy: PinnedHttpsProxy, authority: str, *, method: str = "CONNECT") -> str:
    config = proxy.playwright_proxy
    auth = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
        client.sendall(
            f"{method} {authority} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
        )
        return client.recv(1024).decode("iso-8859-1")


def _open_tunnel(proxy: PinnedHttpsProxy, authority: str) -> socket.socket:
    config = proxy.playwright_proxy
    auth = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
    client = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
    client.sendall(
        f"CONNECT {authority} HTTP/1.1\r\n"
        f"Host: {authority}\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
    )
    response = bytearray()
    while b"\r\n\r\n" not in response:
        response.extend(client.recv(512))
    assert response.startswith(b"HTTP/1.1 200 ")
    return client


def test_proxy_rejects_mixed_public_private_dns_answers():
    calls = []

    def resolver(host, port, *, type):
        return [_answer("93.184.216.34", port), _answer("127.0.0.1", port)]

    def connector(address, **kwargs):
        calls.append(address)
        raise AssertionError("A mixed public/private answer set must be rejected before connect")

    with pytest.raises(UnsafeProxyDestination):
        connect_to_validated_ip("jobs.example.fi", 443, resolver=resolver, connector=connector)

    assert calls == []


def test_proxy_connects_to_validated_numeric_ip_without_second_hostname_resolution():
    calls = []
    resolutions = []
    sentinel = object()

    def resolver(host, port, *, type):
        assert host == "jobs.example.fi"
        resolutions.append((host, port))
        return [_answer("93.184.216.34", port)]

    def connector(address, **kwargs):
        calls.append(address)
        return sentinel

    result = connect_to_validated_ip("jobs.example.fi", 443, resolver=resolver, connector=connector)

    assert result is sentinel
    assert calls == [("93.184.216.34", 443)]
    assert resolutions == [("jobs.example.fi", 443)]


def test_proxy_rejects_cross_origin_and_wrong_port_before_dns_resolution():
    resolutions = []

    def resolver(host, port, *, type):
        resolutions.append((host, port))
        return [_answer("93.184.216.34", port)]

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply", resolver=resolver)
    proxy.start()
    try:
        cross_origin = _connect_request(proxy, "other.example.fi:443")
        wrong_port = _connect_request(proxy, "jobs.example.fi:8443")

        assert " 403 " in cross_origin.splitlines()[0]
        assert " 403 " in wrong_port.splitlines()[0]
        assert resolutions == []
    finally:
        proxy.close()


@pytest.mark.parametrize(
    "authority",
    ["@jobs.example.fi:443", "jobs.example.fi:443?", "jobs.example.fi:443#", "jobs.example.fi:443/path"],
)
def test_proxy_rejects_malformed_connect_authorities_before_dns_or_connect(authority):
    resolutions = []
    connections = []

    def resolver(host, port, *, type):
        resolutions.append((host, port))
        return [_answer("93.184.216.34", port)]

    def connector(address, **kwargs):
        connections.append(address)
        raise OSError("synthetic connector must not be reached")

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply", resolver=resolver, connector=connector)
    proxy.start()
    try:
        response = _connect_request(proxy, authority)
        assert response.startswith(("HTTP/1.1 400 ", "HTTP/1.1 403 "))
        assert resolutions == []
        assert connections == []
    finally:
        proxy.close()


def test_proxy_close_terminates_an_established_tunnel_before_returning():
    proxy_socket, upstream_peer = socket.socketpair()
    proxy = PinnedHttpsProxy(
        "https://jobs.example.fi/apply",
        resolver=lambda host, port, **_kwargs: [_answer("93.184.216.34", port)],
        connector=lambda _address, **_kwargs: proxy_socket,
    )
    proxy.start()
    client = _open_tunnel(proxy, "jobs.example.fi:443")
    try:
        proxy.close()
        assert not proxy.started

        try:
            client.sendall(b"SYNTHETIC_AFTER_CLOSE")
        except OSError:
            pass
        upstream_peer.settimeout(1)
        try:
            received = upstream_peer.recv(128)
        except OSError:
            received = b""
        assert received == b""
    finally:
        client.close()
        upstream_peer.close()


def test_proxy_close_invalidates_a_delayed_dns_result_even_after_restart():
    resolver_entered = threading.Event()
    release_resolver = threading.Event()
    handler_finished = threading.Event()
    connections = []

    def resolver(host, port, *, type):
        resolver_entered.set()
        release_resolver.wait(timeout=3)
        return [_answer("93.184.216.34", port)]

    def connector(address, **kwargs):
        connections.append(address)
        raise AssertionError("A stale CONNECT must not reach the connector after close")

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply", resolver=resolver, connector=connector)
    original_end_tunnel = proxy._end_tunnel

    def mark_handler_finished(client_socket, upstream_socket):
        original_end_tunnel(client_socket, upstream_socket)
        handler_finished.set()

    proxy._end_tunnel = mark_handler_finished
    proxy.start()
    config = proxy.playwright_proxy
    auth = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
    client = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
    client.sendall(
        f"CONNECT jobs.example.fi:443 HTTP/1.1\r\nHost: jobs.example.fi:443\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n\r\n".encode()
    )
    try:
        assert resolver_entered.wait(timeout=2)
        proxy.close()
        proxy.start()
        release_resolver.set()
        assert handler_finished.wait(timeout=2)
        assert connections == []
    finally:
        release_resolver.set()
        client.close()
        proxy.close()


def test_manual_proxy_rejects_non_443_connect():
    proxy = PinnedHttpsProxy(None, resolver=lambda *_args, **_kwargs: pytest.fail("Non-443 destination must be rejected before DNS"))
    proxy.start()
    try:
        response = _connect_request(proxy, "jobs.example.fi:8443")
        assert " 403 " in response.splitlines()[0]
    finally:
        proxy.close()


def test_application_proxy_allows_the_exact_non_default_https_origin_port():
    resolutions = []

    def resolver(host, port, *, type):
        resolutions.append((host, port))
        return [_answer("93.184.216.34", port)]

    def connector(address, **kwargs):
        raise OSError("synthetic upstream connection failure")

    proxy = PinnedHttpsProxy("https://jobs.example.fi:8443/apply", resolver=resolver, connector=connector)
    proxy.start()
    try:
        response = _connect_request(proxy, "jobs.example.fi:8443")
        assert " 502 " in response.splitlines()[0]
        assert resolutions == [("jobs.example.fi", 8443)]
    finally:
        proxy.close()


def test_proxy_rejects_non_connect_requests():
    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply")
    proxy.start()
    try:
        response = _connect_request(proxy, "https://jobs.example.fi/apply", method="GET")
        assert " 405 " in response.splitlines()[0]
    finally:
        proxy.close()


@pytest.mark.parametrize("failure", ["resolve", "connect"])
def test_proxy_fails_closed_when_resolution_or_connect_fails(failure):
    def resolver(host, port, *, type):
        if failure == "resolve":
            raise OSError("synthetic DNS failure")
        return [_answer("93.184.216.34", port)]

    def connector(address, **kwargs):
        raise OSError("synthetic upstream connection failure")

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply", resolver=resolver, connector=connector)
    proxy.start()
    try:
        response = _connect_request(proxy, "jobs.example.fi:443")
        assert " 502 " in response.splitlines()[0]
    finally:
        proxy.close()


def test_proxy_does_not_log_dns_error_details(caplog, capsys):
    private_error = "candidate@example.test access_token=synthetic-secret C:\\Users\\candidate\\cv.pdf"

    def resolver(host, port, *, type):
        raise OSError(private_error)

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply", resolver=resolver)
    proxy.start()
    try:
        with caplog.at_level(logging.DEBUG):
            response = _connect_request(proxy, "jobs.example.fi:443")
        assert response.startswith("HTTP/1.1 502 ")
        output = capsys.readouterr()
        assert private_error not in caplog.text
        assert private_error not in output.out
        assert private_error not in output.err
    finally:
        proxy.close()


def test_proxy_requires_its_ephemeral_credentials():
    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply")
    proxy.start()
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
            client.sendall(b"CONNECT jobs.example.fi:443 HTTP/1.1\r\nHost: jobs.example.fi:443\r\n\r\n")
            response = client.recv(512).decode("iso-8859-1")
        assert " 407 " in response.splitlines()[0]
        assert "Proxy-Authenticate: Basic" in response
    finally:
        proxy.close()


def test_chromium_proxy_failure_does_not_direct_connect_to_a_loopback_resolved_job_host(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from datetime import datetime, timedelta, timezone
    import ssl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    host = "proxy-fallback.example.fi"
    direct_requests = []

    class LocalEmployer(BaseHTTPRequestHandler):
        def do_GET(self):
            direct_requests.append(self.path)
            body = b"local synthetic employer"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "proxy-fallback.crt"
    key_path = tmp_path / "proxy-fallback.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalEmployer)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    local_proxy = None
    try:
        job_url = f"https://{host}:{server.server_port}/apply"
        proxy_resolutions = []
        proxy_connections = []

        def resolver(requested_host, port, *, type):
            assert requested_host == host
            proxy_resolutions.append((requested_host, port))
            return [_answer("93.184.216.34", port)]

        def connector(address, *, timeout):
            proxy_connections.append(address)
            raise OSError("synthetic upstream connection failure")

        local_proxy = PinnedHttpsProxy(job_url, resolver=resolver, connector=connector)
        local_proxy.start()
        browser_proxy = local_proxy.playwright_proxy

        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(
                headless=True,
                args=[f"--host-resolver-rules=MAP {host} 127.0.0.1"],
                proxy=browser_proxy,
            )
            try:
                context = chromium.new_context(ignore_https_errors=True)
                try:
                    page = context.new_page()
                    with pytest.raises(PlaywrightError):
                        page.goto(job_url, timeout=5000)
                    assert proxy_resolutions == [(host, server.server_port)]
                    assert proxy_connections == [("93.184.216.34", server.server_port)]
                    assert direct_requests == []
                    local_proxy.close()
                    with pytest.raises(PlaywrightError):
                        page.goto(job_url + "?proxy-closed", timeout=5000)
                    assert proxy_resolutions == [(host, server.server_port)]
                    assert direct_requests == []
                finally:
                    context.close()
            finally:
                chromium.close()
    finally:
        if local_proxy is not None:
            local_proxy.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
