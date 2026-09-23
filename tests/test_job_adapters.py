import socket

import pytest

from sampoagent.jobs.adapters import JobMarketFinlandAdapter, RssAtomAdapter, SourceAdapterError


def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )


def test_rss_feed_normalizes_job_and_keeps_source_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    payload = b"""<?xml version='1.0'?>
    <rss version='2.0'><channel><item>
      <title>Warehouse Operator</title><link>https://jobs.example.org/vacancy/4</link>
      <description>Forklift experience required</description><company>Example Logistics</company>
      <location>Vantaa</location><application_url>https://apply.example.org/4</application_url>
      <deadline>2030-06-30</deadline>
    </item></channel></rss>"""
    jobs = RssAtomAdapter(fetcher=lambda url, timeout_seconds, max_bytes: payload).search(
        {"id": 9, "name": "Example Feed", "url": "https://jobs.example.org/feed.xml", "capability": "RSS/Atom feed"}
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert (job.title, job.company, job.location, job.language) == ("Warehouse Operator", "Example Logistics", "Vantaa", "en")
    assert job.application_url == "https://apply.example.org/4"
    assert job.source_id == 9
    assert job.source_name == "Example Feed"
    assert job.source_url == "https://jobs.example.org/feed.xml"
    assert job.deadline.isoformat() == "2030-06-30"


def test_atom_feed_is_supported_without_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    payload = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry>
      <title>Siivooja</title><link href='https://jobs.example.org/1'/>
      <summary>Etsimme osa-aikaista siivoojaa.</summary><author><name>Example Oy</name></author>
    </entry></feed>"""
    jobs = RssAtomAdapter(fetcher=lambda url, timeout_seconds, max_bytes: payload).search(
        {"id": 1, "name": "Feed", "url": "https://jobs.example.org/feed", "capability": "RSS/Atom feed"}
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Siivooja"
    assert jobs[0].language == "fi"
    assert jobs[0].application_url == "https://jobs.example.org/1"


def test_malformed_feeds_fail_visibly(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    adapter = RssAtomAdapter(fetcher=lambda url, timeout_seconds, max_bytes: b"<rss><broken>")
    with pytest.raises(SourceAdapterError, match="valid RSS or Atom"):
        adapter.search({"id": 1, "name": "Broken", "url": "https://jobs.example.org/feed", "capability": "RSS/Atom feed"})


def test_json_feed_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    from sampoagent.jobs.adapters import JsonFeedAdapter

    _public_dns(monkeypatch)
    payload = b'''{"version":"https://jsonfeed.org/version/1.1","items":[{"id":"1","title":"Cleaner","url":"https://jobs.example.org/cleaner","content_text":"Cleaning shifts in Espoo.","author":{"name":"Example Oy"},"location":"Espoo"}]}'''
    jobs = JsonFeedAdapter(fetcher=lambda url, timeout_seconds, max_bytes: payload).search(
        {"id": 6, "name": "Employer feed", "url": "https://jobs.example.org/jobs.json", "capability": "JSON Feed"}
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Cleaner"
    assert jobs[0].company == "Example Oy"
    assert jobs[0].location == "Espoo"
    assert jobs[0].source_id == 6


def test_feed_download_is_bounded_and_timeout_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    oversized = RssAtomAdapter(fetcher=lambda url, timeout_seconds, max_bytes: b"x" * (max_bytes + 1))
    with pytest.raises(SourceAdapterError, match="size limit"):
        oversized.search(
            {"id": 1, "name": "Large", "url": "https://jobs.example.org/feed", "capability": "RSS/Atom feed"},
            max_bytes=32,
        )

    def timeout(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
        raise TimeoutError("timed out")

    with pytest.raises(SourceAdapterError, match="timed out"):
        RssAtomAdapter(fetcher=timeout).search(
            {"id": 1, "name": "Slow", "url": "https://jobs.example.org/feed", "capability": "RSS/Atom feed"}
        )


def test_blocked_insecure_urls_and_browser_only_sources_are_not_fetched() -> None:
    calls: list[str] = []
    adapter = RssAtomAdapter(fetcher=lambda url, timeout_seconds, max_bytes: calls.append(url) or b"<rss/>")

    with pytest.raises(SourceAdapterError, match="HTTPS"):
        adapter.search({"id": 1, "name": "Insecure", "url": "http://jobs.example.org/feed", "capability": "RSS/Atom feed"})
    with pytest.raises(SourceAdapterError, match="browser search only"):
        adapter.search({"id": 2, "name": "Duunitori", "url": "https://duunitori.fi", "capability": "Browser search only"})
    assert calls == []


def test_private_dns_destinations_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from sampoagent.jobs.adapters import validate_remote_url

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))])
    with pytest.raises(SourceAdapterError, match="Private or reserved"):
        validate_remote_url("https://jobs.example.org/feed")


def test_robots_denial_stops_before_feed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import sampoagent.jobs.adapters as adapters

    adapters._robots_cache.clear()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    requested = []

    def read_response(request, *, timeout_seconds, max_bytes):
        requested.append(request.full_url)
        return b"User-agent: *\nDisallow: /\n"

    monkeypatch.setattr(adapters, "_read_response", read_response)
    with pytest.raises(SourceAdapterError, match="robots.txt disallows"):
        adapters._fetch_url("https://jobs.example.org/feed", 1, 1024, check_robots=True)
    assert requested == ["https://jobs.example.org/robots.txt"]


def test_redirected_feed_rechecks_destination_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.request import Request
    import sampoagent.jobs.adapters as adapters

    class Deny:
        def can_fetch(self, user_agent, url):
            return False

    monkeypatch.setattr(adapters, "validate_remote_url", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapters, "_robots_parser", lambda *args, **kwargs: Deny())
    with pytest.raises(SourceAdapterError, match="redirected feed"):
        adapters._SafeRedirects(check_robots=True).redirect_request(Request("https://jobs.example.org/feed"), None, 302, "Found", {}, "https://jobs.example.org/private/feed")


def test_authenticated_api_redirect_cannot_forward_key_to_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.request import Request
    import sampoagent.jobs.adapters as adapters

    monkeypatch.setattr(adapters, "validate_remote_url", lambda *_args, **_kwargs: None)
    with pytest.raises(SourceAdapterError, match="official API origin"):
        adapters._SafeRedirects(allowed_origin="https://api.ahtp.fi").redirect_request(Request("https://api.ahtp.fi/kipa", headers={"KIPA-Subscription-Key": "fake-secret"}), None, 307, "Temporary Redirect", {}, "https://attacker.example/collect")

    with pytest.raises(SourceAdapterError, match="official API origin"):
        adapters._SafeRedirects(allowed_origin="https://api.ahtp.fi").redirect_request(Request("https://api.ahtp.fi/kipa", headers={"KIPA-Subscription-Key": "fake-secret"}), None, 307, "Temporary Redirect", {}, "https://api.ahtp.fi:8443/collect")


def test_json_feed_malformed_data_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    from sampoagent.jobs.adapters import JsonFeedAdapter

    _public_dns(monkeypatch)
    adapter = JsonFeedAdapter(fetcher=lambda *_: b"not-json")
    with pytest.raises(SourceAdapterError, match="valid JSON Feed"):
        adapter.search({"id": 1, "name": "Bad", "url": "https://jobs.example.org/feed.json", "capability": "JSON Feed"})


def test_job_market_finland_adapter_requires_officially_issued_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIPA_SUBSCRIPTION_KEY", raising=False)

    with pytest.raises(SourceAdapterError, match="KEHA Centre activation"):
        JobMarketFinlandAdapter.from_environment()


def test_job_market_finland_adapter_maps_ndjson_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_dns(monkeypatch)
    requests: list[dict[str, object]] = []

    def post(url: str, payload: dict[str, object], headers: dict[str, str], timeout_seconds: float, max_bytes: int) -> bytes:
        requests.append({"url": url, "payload": payload, "headers": headers})
        return '''{"position":{"title":{"fi":"Varastotyöntekijä","en":"Warehouse Worker"},"jobDescription":{"fi":"Trukkikokemus eduksi."}},"client":{"company":"Example Oy"},"location":{"workplacePostOffice":"Vantaa"},"application":{"url":{"fi":"https://apply.example.org/42"},"expires":"2030-06-30T00:00:00Z"}}\n'''.encode("utf-8")

    adapter = JobMarketFinlandAdapter(api_key="test-issued-kipa-key", post=post)
    jobs = adapter.search({"id": 2, "name": "Työmarkkinatori", "url": "https://tyomarkkinatori.fi", "capability": "Job Market Finland API"})

    assert len(jobs) == 1
    assert jobs[0].title == "Varastotyöntekijä"
    assert jobs[0].company == "Example Oy"
    assert jobs[0].source_id == 2
    assert jobs[0].language == "fi"
    assert requests[0]["headers"]["KIPA-Subscription-Key"] == "test-issued-kipa-key"
    assert requests[0]["payload"]["onlyStatus"] == "PUBLISHED"
