def test_jobs_page_filters_by_saved_location_preferences() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/settings",
        data={
            "application_mode": "review_everything",
            "daily_limit": "5",
            "ai_usage_mode": "minimal",
            "locations": "Tampere",
            "work_type": "any",
            "keywords": "",
            "salary_minimum": "0",
        },
    )

    response = client.get("/jobs")

    assert "No jobs match your current preferences." in response.text
    assert "Warehouse Worker</td>" not in response.text
