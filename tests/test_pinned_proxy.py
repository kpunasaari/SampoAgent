import base64
import socket

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
