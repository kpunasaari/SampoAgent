def test_queue_can_prepare_application_without_final_submission() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post("/queue/prepare/1", follow_redirects=True)

    assert response.status_code == 200
    assert "Warehouse Worker" in response.text
    assert "READY" in response.text
    assert "Dry Run" in response.text


def test_application_status_can_be_marked_manually_applied() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post("/queue/prepare/1")
    response = client.post("/applications/1/status", data={"status": "APPLIED", "note": "Applied by candidate"}, follow_redirects=True)

    assert response.status_code == 200
    assert "APPLIED" in response.text
