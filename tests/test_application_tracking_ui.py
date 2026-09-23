def test_applications_page_shows_notes_cv_and_timeline() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    client.post("/queue/prepare/1")
    response = client.post(
        "/applications/1/status",
        data={"status": "INTERVIEW", "note": "Phone interview booked"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Phone interview booked" in response.text
    assert "Application added to queue" in response.text
    assert "CV used" in response.text
