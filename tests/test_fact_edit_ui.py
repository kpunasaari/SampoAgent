def test_profile_can_correct_an_extracted_fact_before_using_it() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/cvs/upload",
        files={"file": ("candidate.txt", b"Skills: Warehousing", "text/plain")},
    )
    response = client.post(
        "/profile/facts/5/edit",
        data={"value": "Forklift operation"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Forklift operation" in response.text
    assert "USER_CONFIRMED" in response.text
