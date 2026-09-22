from pathlib import Path


def test_database_initialization_and_demo_data_are_local_and_synthetic(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "sampoagent.db")
    repository.initialize()
    repository.load_demo()

    profile = repository.profile()
    assert profile["name"] == "Aino Example"
    assert repository.count("jobs") >= 2
    assert repository.setting("application_mode") == "review_everything"


def test_dashboard_and_all_primary_sections_render() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    for route in ("/", "/profile", "/careers", "/cvs", "/jobs", "/sources", "/queue", "/applications", "/analytics", "/agent", "/settings", "/onboarding"):
        response = client.get(route)
        assert response.status_code == 200
        assert "SampoAgent" in response.text


def test_profile_skill_form_creates_a_confirmed_fact_and_recommendation() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post("/profile/skills", data={"skill": "forklift operation"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Warehouse Worker" in response.text
