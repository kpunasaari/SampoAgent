import socket

from sampoagent.applications.urls import (
    is_safe_public_https_url,
    is_safe_public_https_destination,
    is_same_public_origin,
    looks_like_authentication_page,
    looks_like_captcha_page,
)


def test_safe_public_https_job_url_is_allowed():
    assert is_safe_public_https_url("https://jobs.northstar.fi/apply?id=42")


def test_credentials_tokens_private_hosts_and_insecure_schemes_are_rejected():
    unsafe = (
        "http://jobs.example.fi/apply",
        "https://name:secret@jobs.example.fi/apply",
        "https://127.0.0.1/apply",
        "https://192.168.1.20/apply",
        "https://service.test/apply",
        "https://jobs.example.fi/apply?session=private",
    )
    assert all(not is_safe_public_https_url(url) for url in unsafe)


def test_captcha_and_login_pages_are_detected_without_interacting_with_them():
    assert looks_like_captcha_page("https://jobs.fi/apply", "Please verify that you are human", False)
    assert looks_like_captcha_page("https://jobs.fi/apply", "Apply now", True)
    assert looks_like_captcha_page(
        "https://jobs.fi/apply", "Checking your browser before accessing this site", False,
    )
    assert looks_like_captcha_page(
        "https://jobs.fi/cdn-cgi/challenge-platform/", "Apply now", False,
    )
    assert looks_like_authentication_page("https://jobs.fi/sign-in", 0)
    assert looks_like_authentication_page("https://jobs.fi/apply", 1)
    assert not looks_like_authentication_page("https://jobs.fi/apply", 0)


def test_application_page_redirect_must_stay_on_same_public_origin():
    original = "https://careers.employer.fi/apply/1"
    assert is_same_public_origin(original, "https://careers.employer.fi/apply/1/step-2")
    assert not is_same_public_origin(original, "https://attacker.fi/collect")
    assert not is_same_public_origin(original, "http://careers.employer.fi/apply/1")


def test_browser_destination_rejects_any_private_or_reserved_dns_answer(monkeypatch):
    def resolve(host, port, *, type):
        return [
            (socket.AF_INET, type, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            (socket.AF_INET6, type, socket.IPPROTO_TCP, "", ("fd00::1", port, 0, 0)),
        ]

    monkeypatch.setattr("sampoagent.applications.urls.socket.getaddrinfo", resolve)

    assert not is_safe_public_https_destination("https://jobs.example.fi/apply")


def test_browser_destination_rejects_dns_failure_and_allows_only_public_answers(monkeypatch):
    def public_resolve(host, port, *, type):
        return [(socket.AF_INET, type, socket.IPPROTO_TCP, "", ("8.8.8.8", port))]

    monkeypatch.setattr("sampoagent.applications.urls.socket.getaddrinfo", public_resolve)
    assert is_safe_public_https_destination("https://jobs.example.fi/apply")

    def failed_resolve(host, port, *, type):
        raise socket.gaierror("no name")

    monkeypatch.setattr("sampoagent.applications.urls.socket.getaddrinfo", failed_resolve)
    assert not is_safe_public_https_destination("https://jobs.example.fi/apply")
