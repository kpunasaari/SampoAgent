"""Conservative URL checks for public job application destinations."""

import ipaddress
import socket
from urllib.parse import parse_qsl, urlsplit


_RESERVED_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
_RESERVED_SUFFIXES = (".test", ".invalid", ".example", ".localhost", ".local")
_SENSITIVE_QUERY_KEYS = {"token", "access_token", "session", "sessionid", "validator", "code", "secret", "auth", "password", "key", "signature", "sig", "jwt"}
_CAPTCHA_MARKERS = (
    "captcha", "recaptcha", "hcaptcha", "turnstile", "cf-chl-", "/cdn-cgi/challenge-platform/",
    "challenges.cloudflare.com", "verify that you are human", "verify you are human",
    "prove you are not a robot", "checking your browser", "complete the security check",
    "en ole robotti", "varmista että olet ihminen",
)
_LOGIN_PATH_MARKERS = ("/login", "/log-in", "/signin", "/sign-in", "/authenticate", "/auth/")


def is_safe_public_https_url(url: str) -> bool:
    """Reject cleartext, credential-bearing, private, and placeholder URLs.

    This is a conservative local guard, not a DNS-rebinding-resistant network
    sandbox. Callers must still let the user select trusted job sources.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password or parsed.fragment:
            return False
        if host in _RESERVED_HOSTS or host.endswith(_RESERVED_SUFFIXES):
            return False
        if any(key.casefold() in _SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query)):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            if "." not in host:
                return False
        return True
    except (TypeError, ValueError):
        return False


def is_safe_public_https_destination(url: str) -> bool:
    """Fail closed unless every resolved address is globally routable.

    This is a DNS preflight for browser navigation, not a network sandbox: the
    browser performs its own resolution after this check, so DNS rebinding is
    still possible between validation and connection.
    """
    if not is_safe_public_https_url(url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or 443
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not answers:
            return False
        for answer in answers:
            address = ipaddress.ip_address(answer[4][0])
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                return False
        return True
    except (OSError, TypeError, ValueError, IndexError):
        return False


def is_same_public_origin(expected_url: str, actual_url: str) -> bool:
    """Require the inspected application page to stay on its original HTTPS host."""
    if not is_safe_public_https_url(expected_url) or not is_safe_public_https_url(actual_url):
        return False
    try:
        expected = urlsplit(expected_url)
        actual = urlsplit(actual_url)
        return (
            (expected.hostname or "").casefold().rstrip(".") == (actual.hostname or "").casefold().rstrip(".")
            and (expected.port or 443) == (actual.port or 443)
        )
    except ValueError:
        return False


def looks_like_captcha_page(url: str, text: str, challenge_element_found: bool) -> bool:
    if challenge_element_found:
        return True
    lowered = text.casefold()
    path = urlsplit(url).path.casefold()
    return any(marker in lowered or marker in path for marker in _CAPTCHA_MARKERS)


def looks_like_authentication_page(url: str, password_field_count: int) -> bool:
    if password_field_count > 0:
        return True
    path = urlsplit(url).path.casefold()
    return any(marker in path for marker in _LOGIN_PATH_MARKERS)
