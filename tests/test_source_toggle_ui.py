def test_user_can_disable_and_enable_a_job_source() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    disabled = client.post("/sources/1/toggle", follow_redirects=True)
    enabled = client.post("/sources/1/toggle", follow_redirects=True)

    assert "Inactive" in disabled.text
    assert "Active" in enabled.text
