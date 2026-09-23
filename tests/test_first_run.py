from pathlib import Path

from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository


def test_new_install_is_honest_and_seeds_sources_not_fake_candidate_or_jobs(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "new.db")
    repository = app.state.repository

    assert repository.profile() is None
    assert repository.count("jobs") == 0
    assert repository.count("job_sources") == 13
    assert repository.setting("application_mode") == "review_everything"
    assert repository.setting("daily_limit") == "0"
    assert repository.setting("dry_run") == "true"

    dashboard = TestClient(app).get("/")
    assert "Set up your career workspace" in dashboard.text
    assert "Aino Example" not in dashboard.text
    assert "Synthetic demo" not in dashboard.text


def test_opening_existing_database_does_not_change_user_data(tmp_path: Path) -> None:
    db_path = tmp_path / "existing.db"
    repository = Repository(db_path)
    repository.initialize()
    repository.save_profile("Real Person", "fi", "person@example.test")
    repository.add_target_occupation("Cleaner", "Siivooja")
    repository.connection.close()

    app = create_app(database_path=db_path)

    assert app.state.repository.profile()["name"] == "Real Person"
    assert app.state.repository.count("jobs") == 0
    assert len(app.state.repository.target_occupations()) == 1


def test_builtin_source_seeding_is_idempotent_and_preserves_user_settings() -> None:
    repository = Repository(":memory:")
    repository.initialize()

    repository.seed_builtin_sources()
    source = repository.source(1)
    repository.set_source_enabled(1, False)
    repository.seed_builtin_sources()

    assert repository.count("job_sources") == 13
    assert repository.source(1)["enabled"] == 0
    assert repository.source(1)["name"] == source["name"]
