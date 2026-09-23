import json
import socket

from sampoagent.db.repository import Repository
from sampoagent.jobs.adapters import ScraplingAdapter
from sampoagent.jobs.runner import run_discovery


class _Selection:
    def __init__(self, values):
        self.values = values

    def getall(self):
        return self.values

    def __iter__(self):
        return iter(self.values)


class _JsonLdPage:
    def __init__(self, _html):
        self.script = json.dumps({
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Cleaner",
            "description": "Office cleaning in Vantaa",
            "url": "https://jobs.example.org/cleaner-1",
            "hiringOrganization": {"name": "Example Oy"},
            "jobLocation": {"address": {"addressLocality": "Vantaa", "addressCountry": "FI"}},
            "validThrough": "2027-01-31",
        })

    def css(self, selector):
        if selector == 'script[type="application/ld+json"]::text':
            return _Selection([self.script])
        return _Selection([])


def _public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])


def test_scrapling_adapter_parses_structured_jobposting_and_preserves_provenance(monkeypatch):
    _public_dns(monkeypatch)
    adapter = ScraplingAdapter(fetcher=lambda *_: b"<html><script type='application/ld+json'>{}</script></html>", selector_factory=_JsonLdPage)

    jobs = adapter.search({"id": 42, "name": "Employer", "url": "https://jobs.example.org/careers", "capability": "Scrapling public page"})

    assert len(jobs) == 1
    assert jobs[0].title == "Cleaner"
    assert jobs[0].company == "Example Oy"
    assert jobs[0].location == "Vantaa, FI"
    assert jobs[0].deadline.isoformat() == "2027-01-31"
    assert jobs[0].source_id == 42
    assert jobs[0].source_url == "https://jobs.example.org/careers"


def test_scrapling_adapter_can_use_user_supplied_card_selector(monkeypatch):
    _public_dns(monkeypatch)

    class CardPage:
        def __init__(self, _html):
            pass

        def css(self, selector):
            if selector == 'script[type="application/ld+json"]::text':
                return _Selection([])
            if selector == "li.role-card":
                return _Selection([Card()])
            return _Selection([])

    class Card:
        def css(self, selector):
            mapping = {
                "h1::text, h2::text, h3::text, [class*='title']::text": ["Cleaner"],
                "a::attr(href)": ["/jobs/cleaner-2"],
                "[class*='company']::text, [class*='employer']::text": ["Example Oy"],
                "[class*='location']::text": ["Espoo"],
                "[class*='description']::text, p::text": ["Cleaning work"],
                "[class*='deadline']::text, time::attr(datetime)": [],
            }
            return _Selection(mapping.get(selector, []))

    adapter = ScraplingAdapter(fetcher=lambda *_: b"<html></html>", selector_factory=CardPage)

    jobs = adapter.search({"id": 5, "name": "Employer", "url": "https://jobs.example.org/careers", "capability": "Scrapling public page", "listing_selector": "li.role-card"})

    assert len(jobs) == 1
    assert jobs[0].application_url == "https://jobs.example.org/jobs/cleaner-2"
    assert jobs[0].company == "Example Oy"
    assert jobs[0].location == "Espoo"


def test_scrapling_source_uses_robots_checked_bounded_fetcher(monkeypatch):
    _public_dns(monkeypatch)
    calls = []

    def safe_fetch(url, timeout_seconds, max_bytes, *, check_robots):
        calls.append((url, timeout_seconds, max_bytes, check_robots))
        return b"<html></html>"

    monkeypatch.setattr("sampoagent.jobs.adapters._fetch_url", safe_fetch)
    adapter = ScraplingAdapter(selector_factory=_JsonLdPage)
    source = {"id": 1, "name": "Employer", "url": "https://jobs.example.org/careers", "capability": "Scrapling public page"}

    adapter.search(source, timeout_seconds=3.0, max_bytes=1024)

    assert calls == [("https://jobs.example.org/careers", 3.0, 1024, True)]


def test_scrapling_adapter_rejects_browser_only_and_insecure_urls(monkeypatch):
    from sampoagent.jobs.adapters import SourceAdapterError
    import pytest

    with pytest.raises(SourceAdapterError, match="not configured"):
        ScraplingAdapter(fetcher=lambda *_: b"").search({"url": "https://example.org", "capability": "Browser search only"})
    with pytest.raises(SourceAdapterError, match="HTTPS"):
        ScraplingAdapter(fetcher=lambda *_: b"").search({"url": "http://example.org", "capability": "Scrapling public page"})


def test_discovery_runner_dispatches_scrapling_sources_and_imports_only_normalized_jobs(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "scrapling-discovery.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    source_id = repository.add_source(name="Public careers", url="https://jobs.example.org/careers", country="Finland", source_type="employer career site", capability="Scrapling public page")
    adapter = ScraplingAdapter(fetcher=lambda *_: b"<html><script type='application/ld+json'>{}</script></html>", selector_factory=_JsonLdPage)

    report = run_discovery(repository, scrapling_adapter=adapter)

    assert report.imported_count == 1
    assert repository.job(1)["source_id"] == source_id
    assert repository.job(1)["title"] == "Cleaner"
