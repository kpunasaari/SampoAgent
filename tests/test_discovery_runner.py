import socket

from sampoagent.db.repository import Repository
from sampoagent.jobs.adapters import RssAtomAdapter
from sampoagent.jobs.runner import run_discovery


def _public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])


def test_discovery_imports_feed_results_and_records_browser_only_sources(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "discover.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    feed_id = repository.add_source(name="Permitted Feed", url="https://feed.example.org/jobs", country="Finland", source_type="feed")
    repository.connection.execute("UPDATE job_sources SET capability='RSS/Atom feed' WHERE id=?", (feed_id,))
    repository.connection.commit()

    payload = b"<rss><channel><item><title>Cleaner</title><link>https://jobs.example.org/42</link><description>Cleaning jobs</description><company>Example Oy</company><location>Vantaa</location><deadline>2030-06-30</deadline></item></channel></rss>"
    report = run_discovery(repository, rss_adapter=RssAtomAdapter(fetcher=lambda *_: payload))

    assert report.imported_count == 1
    assert repository.count("jobs") == 1
    assert repository.job(1)["deadline"] == "2030-06-30"
    assert report.plan.queries
    results = repository.discovery_source_results(report.run_id)
    assert any(result["status"] == "imported" for result in results)
    assert any(result["status"] == "browser_only" for result in results)


def test_discovery_never_fetches_disabled_or_browser_only_sources(tmp_path):
    repository = Repository(tmp_path / "browser.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    source_id = repository.add_source(name="Disabled", url="https://feed.example.org", country="Finland", source_type="feed")
    repository.set_source_enabled(source_id, False)

    class RefusesFetch:
        def search(self, *args, **kwargs):
            raise AssertionError("should not be called")

    report = run_discovery(repository, rss_adapter=RefusesFetch())
    assert report.imported_count == 0
    results = repository.discovery_source_results(report.run_id)
    assert all(row["source_id"] != source_id for row in results)
    assert all(row["status"] == "browser_only" for row in results)


def test_official_api_source_reports_activation_requirement_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("KIPA_SUBSCRIPTION_KEY", raising=False)
    repository = Repository(tmp_path / "official-api.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    source = next(item for item in repository.rows("job_sources") if item["url"] == "https://tyomarkkinatori.fi")
    repository.update_source(int(source["id"]), name="Job Market Finland", url="https://tyomarkkinatori.fi", country="Finland", source_type="public-sector board", notes="Official public API", capability="Job Market Finland API")

    report = run_discovery(repository)

    assert report.status == "partial"
    source_result = next(result for result in repository.discovery_source_results(report.run_id) if result["source_name"] == "Job Market Finland")
    assert source_result["status"] == "not_configured"
    assert "KEHA Centre activation" in source_result["message"]


def test_discovery_imports_only_profile_relevant_feed_items(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "relevance.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    feed_id = repository.add_source(name="Permitted Feed", url="https://feed.example.org/jobs", country="Finland", source_type="feed", capability="RSS/Atom feed")
    payload = b"""<rss><channel>
      <item><title>Cleaner</title><link>https://jobs.example.org/1</link><description>Office cleaning</description><company>Example Oy</company></item>
      <item><title>Software Architect</title><link>https://jobs.example.org/2</link><description>Build software</description><company>Another Oy</company></item>
    </channel></rss>"""

    report = run_discovery(repository, rss_adapter=RssAtomAdapter(fetcher=lambda *_: payload))
    assert report.jobs_found == 1
    assert report.imported_count == 1
    assert repository.job(1)["title"] == "Cleaner"


def test_feed_duplicates_are_skipped_and_counted_for_user(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "duplicates.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_source(name="Permitted Feed", url="https://feed.example.org/jobs", country="Finland", source_type="feed", capability="RSS/Atom feed")
    payload = b"""<rss><channel>
      <item><title>Cleaner</title><link>https://jobs.example.org/1</link><description>Office cleaning</description><company>Example Oy</company></item>
      <item><title>Cleaner</title><link>https://jobs.example.org/1?source=repeat</link><description>Office cleaning</description><company>Example Oy</company></item>
    </channel></rss>"""

    report = run_discovery(repository, rss_adapter=RssAtomAdapter(fetcher=lambda *_: payload))
    assert report.jobs_found == 2
    assert report.imported_count == 1
    assert report.duplicates_count == 1


def test_environment_configured_api_sources_obey_the_same_eight_source_cap(tmp_path, monkeypatch):
    import sampoagent.jobs.runner as runner

    repository = Repository(tmp_path / "api-limit.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    for index in range(9):
        repository.add_source(name=f"Official API {index}", url=f"https://tyomarkkinatori.fi/?scope={index}", country="Finland", source_type="public-sector board", capability="Job Market Finland API")

    class FakeApi:
        def __init__(self):
            self.calls = 0

        def search(self, *args, **kwargs):
            self.calls += 1
            return []

    adapter = FakeApi()
    monkeypatch.setattr(runner.JobMarketFinlandAdapter, "from_environment", lambda: adapter)
    report = run_discovery(repository)
    rows = repository.discovery_source_results(report.run_id)

    assert adapter.calls == 8
    assert sum(row["status"] == "skipped_limit" for row in rows) >= 1
