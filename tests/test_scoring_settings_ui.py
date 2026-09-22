def test_settings_persist_score_dimension_configuration() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/settings",
        data={
            "application_mode": "review_everything",
            "daily_limit": "5",
            "ai_usage_mode": "minimal",
            "eligibility_enabled": "yes",
            "eligibility_weight": "70",
            "eligibility_minimum": "75",
            "competitive_strength_enabled": "no",
            "competitive_strength_weight": "20",
            "competitive_strength_minimum": "40",
            "confidence_enabled": "yes",
            "confidence_weight": "10",
            "confidence_minimum": "50",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Eligibility minimum" in response.text
    assert "75" in response.text
    assert "Competitive Strength is disabled" in response.text
