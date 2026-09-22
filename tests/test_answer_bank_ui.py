def test_answer_bank_saves_source_and_risk_label() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/answers",
        data={
            "category": "FACT",
            "question": "Are you authorized to work in Finland?",
            "value": "I am authorized to work in Finland.",
            "source": "USER_CONFIRMED",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "USER_CONFIRMED" in response.text
    assert "HIGH" in response.text
