import socket

from sampoagent.db.repository import Repository
from sampoagent.jobs.adapters import RssAtomAdapter, SourceAdapterError
from sampoagent.jobs.discovery import build_search_plan
from sampoagent.jobs.runner import run_discovery


def _public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])


def test_transferable_skills_suggest_but_do_not_activate_occupation_searches():
    plan = build_search_plan(
        facts=[{"type": "transferable_skill", "value": "customer service", "confirmed": 1, "enabled": 1}],
        candidate_records={},
        targets=[],
        career_profiles=[],
        preferences={},
        sources=[{"id": 1, "name": "Public careers", "url": "https://example.org", "enabled": 1, "capability": "Browser search only"}],
        max_queries=20,
    )

    assert "Customer Service Representative" not in plan.terms
    assert not plan.queries


def test_user_selected_occupation_becomes_an_active_search_term():
    plan = build_search_plan(
        facts=[{"type": "transferable_skill", "value": "customer service", "confirmed": 1, "enabled": 1}],
        candidate_records={},
        targets=[{"title_en": "Customer Service Representative", "title_fi": "Asiakaspalvelija", "enabled": 1}],
        career_profiles=[],
        preferences={},
        sources=[{"id": 1, "name": "Public careers", "url": "https://example.org", "enabled": 1, "capability": "Browser search only"}],
        max_queries=20,
    )

    assert "Customer Service Representative" in plan.terms
    assert "Asiakaspalvelija" in plan.terms


def test_discovery_never_calls_a_source_without_an_explicit_target_or_keyword(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "no-target.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_confirmed_fact(fact_type="skill", value="cleaning")
    source_id = repository.add_source(
        name="Public careers", url="https://jobs.example.org/careers", country="Finland",
        source_type="employer career site", capability="Scrapling public page",
    )

    class MustNotFetch:
        def search(self, *_args, **_kwargs):
            raise AssertionError("a career suggestion must not activate source discovery")

    report = run_discovery(repository, scrapling_adapter=MustNotFetch())

    assert report.status == "no_search_terms"
    assert report.plan.terms == ()
    assert repository.discovery_source_results(report.run_id) == []
    assert repository.source(source_id) is not None


def test_discovery_imports_feed_results_and_records_browser_only_sources(tmp_path, monkeypatch):
    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "discover.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_target_occupation("Cleaner", "Siivooja")
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


def test_official_published_api_listing_keeps_verification_evidence(tmp_path, monkeypatch):
    from sampoagent.jobs.adapters import JobMarketFinlandAdapter

    _public_dns(monkeypatch)
    repository = Repository(tmp_path / "official-verified.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_target_occupation("Cleaner", "Siivooja")
    source = next(item for item in repository.rows("job_sources") if item["url"] == "https://tyomarkkinatori.fi")
    repository.update_source(int(source["id"]), name="Job Market Finland", url="https://tyomarkkinatori.fi", country="Finland", source_type="public-sector board", notes="Official public API", capability="Job Market Finland API")
    payload = b'''{"position":{"title":{"en":"Cleaner"},"jobDescription":{"en":"Cleaning work"}},"client":{"company":"Public Employer"},"location":{"workplacePostOffice":"Vantaa"},"application":{"url":{"en":"https://careers.public-employer.fi/jobs/1"},"expires":"2030-06-30T00:00:00Z"}}'''
    adapter = JobMarketFinlandAdapter(api_key="test-key", post=lambda *_args: payload)

    report = run_discovery(repository, api_adapter=adapter)
    job = repository.job(1)

    assert report.imported_count == 1
    assert job["verification_state"] == "VERIFIED"
    evidence = repository.job_verification(1)
    assert evidence["method"] == "official_job_market_finland_api"
    assert evidence["source_url"] == "https://tyomarkkinatori.fi"


def test_discovery_never_fetches_disabled_or_browser_only_sources(tmp_path):
    repository = Repository(tmp_path / "browser.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_target_occupation("Cleaner", "Siivooja")
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
    repository.add_target_occupation("Cleaner", "Siivooja")
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
    repository.add_target_occupation("Cleaner", "Siivooja")
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
    repository.add_target_occupation("Cleaner", "Siivooja")
    repository.add_source(name="Permitted Feed", url="https://feed.example.org/jobs", country="Finland", source_type="feed", capability="RSS/Atom feed")
    payload = b"""<rss><channel>
      <item><title>Cleaner</title><link>https://jobs.example.org/1</link><description>Office cleaning</description><company>Example Oy</company></item>
      <item><title>Cleaner</title><link>https://jobs.example.org/1?source=repeat</link><description>Office cleaning</description><company>Example Oy</company></item>
    </channel></rss>"""

    report = run_discovery(repository, rss_adapter=RssAtomAdapter(fetcher=lambda *_: payload))
    assert report.jobs_found == 2
    assert report.imported_count == 1
    assert report.duplicates_count == 1


def test_automatic_source_cap_rotates_fairly_between_runs(tmp_path, monkeypatch):
    import sampoagent.jobs.runner as runner

    repository = Repository(tmp_path / "api-limit.db")
    repository.initialize()
    repository.save_profile("Kai", "en")
    repository.add_confirmed_fact(fact_type="experience", value="Cleaner")
    repository.add_target_occupation("Cleaner", "Siivooja")
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
    first_run_ids = {int(row["source_id"]) for row in rows if row["source_id"] is not None and row["capability"] == "Job Market Finland API" and row["status"] == "no_results"}
    skipped_ids = {int(row["source_id"]) for row in rows if row["source_id"] is not None and row["status"] == "skipped_limit"}
    assert len(first_run_ids) == 8
    assert len(skipped_ids) == 1

    repository.connection.execute(
        "UPDATE discovery_source_state SET next_attempt_at=?",
        ("2000-01-01T00:00:00+00:00",),
    )
    repository.connection.commit()

    second = run_discovery(repository)
    second_rows = repository.discovery_source_results(second.run_id)
    checked_second = {int(row["source_id"]) for row in second_rows if row["source_id"] is not None and row["capability"] == "Job Market Finland API" and row["status"] == "no_results"}
    assert adapter.calls == 16
    assert len(checked_second) == 8
    assert next(iter(skipped_ids)) in checked_second


def test_failed_source_uses_persisted_exponential_backoff_and_success_clears_it(tmp_path):
    repository = Repository(tmp_path / "source-backoff.db")
    repository.initialize()
    repository.save_profile("Synthetic User", "en")
    repository.add_target_occupation("Cleaner", "Siivooja")
    source_id = repository.add_source(
        name="Intermittent Feed",
        url="https://feed.example.org/jobs",
        country="Finland",
        source_type="feed",
        capability="RSS/Atom feed",
    )

    class IntermittentFeed:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise SourceAdapterError("Source request timed out.")
            return []

    adapter = IntermittentFeed()
    first = run_discovery(repository, rss_adapter=adapter)
    failure = repository.discovery_source_state(source_id)
    assert failure["consecutive_failures"] == 1
    assert failure["next_attempt_at"] is not None

    second = run_discovery(repository, rss_adapter=adapter)
    skipped = next(row for row in repository.discovery_source_results(second.run_id) if row["source_id"] == source_id)
    assert skipped["status"] == "cooldown"
    assert adapter.calls == 1

    repository.connection.execute(
        "UPDATE discovery_source_state SET next_attempt_at=? WHERE source_id=?",
        ("2000-01-01T00:00:00+00:00", source_id),
    )
    repository.connection.commit()
    third = run_discovery(repository, rss_adapter=adapter)
    checked = next(row for row in repository.discovery_source_results(third.run_id) if row["source_id"] == source_id)
    recovered = repository.discovery_source_state(source_id)
    assert checked["status"] == "no_results"
    assert adapter.calls == 2
    assert recovered["consecutive_failures"] == 0
    assert recovered["next_attempt_at"] is not None
    assert recovered["last_success_at"] is not None


def test_successful_source_has_persisted_minimum_poll_interval(tmp_path):
    repository = Repository(tmp_path / "source-min-interval.db")
    repository.initialize()
    repository.save_profile("Synthetic User", "en")
    repository.add_target_occupation("Cleaner", "Siivooja")
    source_id = repository.add_source(
        name="Permitted Feed",
        url="https://feed.example.org/jobs",
        country="Finland",
        source_type="feed",
        capability="RSS/Atom feed",
    )

    class EmptyFeed:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            return []

    adapter = EmptyFeed()
    first = run_discovery(repository, rss_adapter=adapter)
    assert next(row for row in repository.discovery_source_results(first.run_id) if row["source_id"] == source_id)["status"] == "no_results"
    next_state = repository.discovery_source_state(source_id)
    assert next_state["next_attempt_at"] is not None

    second = run_discovery(repository, rss_adapter=adapter)
    second_result = next(row for row in repository.discovery_source_results(second.run_id) if row["source_id"] == source_id)
    assert second_result["status"] == "cooldown"
    assert adapter.calls == 1
