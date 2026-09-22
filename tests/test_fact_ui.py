def test_profile_fact_can_be_confirmed_from_ui() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post("/cvs/upload", files={"file": ("cv.txt", b"Skills: cleaning", "text/plain")})
    response = client.post("/profile/facts/5/confirm", follow_redirects=True)

    assert response.status_code == 200
    assert "Confirmed" in response.text
