from pathlib import Path


def test_custom_source_and_settings_are_persisted(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    source_id = repository.add_source(name="Employer careers", url="https://example.test/careers", country="Finland", source_type="employer career site", notes="Direct careers page")
    repository.set_setting("daily_limit", "12")

    assert source_id > 0
    assert repository.rows("job_sources")[0]["name"] == "Employer careers"
    assert repository.setting("daily_limit") == "12"
