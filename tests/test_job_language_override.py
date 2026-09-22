def test_job_language_can_be_overridden_before_queueing() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    updated = client.post("/jobs/1/language", data={"language": "fi"}, follow_redirects=True)
    queued = client.post("/queue/prepare/1", follow_redirects=False)
    application = client.get("/applications")

    assert "Language: fi" in updated.text
    assert queued.status_code == 303
    assert "QUEUED" in application.text
