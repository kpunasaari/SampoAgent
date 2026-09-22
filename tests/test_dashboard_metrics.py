def test_dashboard_shows_actual_applied_today_count() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post("/queue/prepare/1")
    client.post("/applications/1/status", data={"status": "APPLIED", "note": "Submitted"})

    response = client.get("/")

    assert "1 / 5<small> Applied today / daily limit</small>" in response.text
