def test_onboarding_completion_starts_in_dry_run_mode() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post("/onboarding/complete", data={"name": "Aino Example", "locale": "fi"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Dry Run" in response.text
