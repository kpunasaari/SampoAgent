def test_user_can_override_low_score_for_review_but_not_hard_requirements() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/jobs/import",
        data={
            "title": "Unrelated role",
            "company": "Example Oy",
            "location": "Helsinki",
            "description": "A general role with no matching requirements.",
            "application_url": "https://example.test/apply/unrelated",
        },
    )

    override = client.post("/jobs/3/override", data={"note": "Candidate wants to review this role"}, follow_redirects=True)
    prepared = client.post("/queue/prepare/3", follow_redirects=True)

    assert "User review override active" in override.text
    assert "Application prepared for review" in prepared.text


def test_override_cannot_bypass_explicit_hard_requirement() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/jobs/import",
        data={
            "title": "Driver",
            "company": "Example Oy",
            "location": "Helsinki",
            "description": "B-ajokortti vaaditaan.",
            "application_url": "https://example.test/apply/driver-hard",
        },
    )

    client.post("/jobs/3/override", data={"note": "Please review"})
    response = client.post("/queue/prepare/3", follow_redirects=True)

    assert "Missing mandatory requirement: B-ajokortti" in response.text
    assert "No prepared applications yet." in response.text
