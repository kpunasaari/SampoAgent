def test_profile_can_store_structured_candidate_records() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    response = client.post(
        "/profile/records",
        data={"record_type": "licence", "title": "B-ajokortti", "details": "Valid driving licence"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "B-ajokortti" in response.text
    assert "Valid driving licence" in response.text


def test_directly_entered_licence_satisfies_job_requirement() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    client.post("/profile/records", data={"record_type": "licence", "title": "B-ajokortti", "details": ""})
    client.post(
        "/jobs/import",
        data={
            "title": "Delivery Driver",
            "company": "Example Transport Oy",
            "location": "Vantaa",
            "description": "B-ajokortti vaaditaan.",
            "application_url": "https://example.test/apply/licensed-driver",
        },
    )

    response = client.post("/queue/prepare/3", follow_redirects=True)

    assert "Application prepared for review" in response.text
