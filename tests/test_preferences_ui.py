def test_settings_saves_candidate_job_preferences() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/settings",
        data={
            "application_mode": "review_everything",
            "daily_limit": "5",
            "ai_usage_mode": "minimal",
            "locations": "Helsinki, Vantaa",
            "work_type": "hybrid",
            "keywords": "warehouse, customer service",
            "salary_minimum": "2400",
        },
        follow_redirects=True,
    )

    assert "Helsinki, Vantaa" in response.text
    assert "hybrid" in response.text
    assert "2400" in response.text
    assert "<option value='hybrid' selected>Hybrid</option>" in response.text
