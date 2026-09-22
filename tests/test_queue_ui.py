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


def test_queue_refuses_job_with_missing_mandatory_requirement() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    imported = client.post(
        "/jobs/import",
        data={
            "title": "Delivery Driver",
            "company": "Example Transport Oy",
            "location": "Vantaa",
            "description": "B-ajokortti vaaditaan tähän tehtävään.",
            "application_url": "https://example.test/apply/driver",
        },
        follow_redirects=False,
    )
    assert imported.status_code == 303

    response = client.post("/queue/prepare/3", follow_redirects=True)

    assert response.status_code == 200
    assert "Missing mandatory requirement: B-ajokortti" in response.text
    assert "No prepared applications yet." in response.text


def test_queue_refuses_duplicate_application_record() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    first = client.post("/queue/prepare/1", follow_redirects=True)
    second = client.post("/queue/prepare/1", follow_redirects=True)

    assert "Application prepared for review" in first.text
    assert "This job already has an application record." in second.text
    assert second.text.count("<td>READY</td>") == 1


def test_daily_limit_prevents_manual_applied_status() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post("/settings", data={"application_mode": "review_everything", "daily_limit": "1", "ai_usage_mode": "minimal"})
    client.post("/queue/prepare/1")
    client.post("/queue/prepare/2")
    client.post("/applications/1/status", data={"status": "APPLIED", "note": "Candidate submitted"})

    response = client.post("/applications/2/status", data={"status": "APPLIED", "note": "Candidate submitted"}, follow_redirects=True)

    assert "Daily application limit reached" in response.text
    assert "<td>2</td><td>QUEUED</td>" in response.text
