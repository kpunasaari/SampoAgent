from pathlib import Path


def test_job_preferences_are_saved_as_structured_local_data(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.save_preferences({"locations": ["Helsinki", "Vantaa"], "remote": True, "work_schedules": ["day", "shift"], "minimum_salary": 15})

    preferences = repository.preferences()
    assert preferences["locations"] == ["Helsinki", "Vantaa"]
    assert preferences["remote"] is True


def test_source_adapter_reports_browser_only_capability_without_scraping() -> None:
    from sampoagent.jobs.sources import BrowserOnlySourceAdapter

    adapter = BrowserOnlySourceAdapter("Kuntarekry", "https://kuntarekry.fi")
    result = adapter.discover()

    assert result.capability == "Browser search only"
    assert result.jobs == []
