def test_career_profiles_can_be_added_disabled_and_deleted() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    created = client.post("/careers/profiles", data={"name": "Office Administration", "notes": "Prefer Helsinki roles"}, follow_redirects=True)

    assert "Office Administration" in created.text
    assert "Prefer Helsinki roles" in created.text

    disabled = client.post("/careers/profiles/3/toggle", follow_redirects=True)
    assert "Inactive" in disabled.text

    removed = client.post("/careers/profiles/3/delete", follow_redirects=True)
    assert "Office Administration" not in removed.text
