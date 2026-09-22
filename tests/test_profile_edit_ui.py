def test_profile_page_can_update_candidate_identity_and_locale() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/profile/details",
        data={"name": "Robin Example", "email": "robin@example.test", "locale": "fi"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Robin Example" in response.text
    assert "robin@example.test" in response.text
