def test_source_health_reports_browser_only_and_invalid_url() -> None:
    from sampoagent.jobs.sources import source_health

    assert source_health("https://example.test/jobs", "Browser search only") == "Browser search only"
    assert source_health("not a url", "Browser search only") == "Temporarily unavailable"
