def test_user_can_register_metadata_only_cv_template() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/cvs/templates",
        data={"name": "My Logistics Template", "language": "fi", "role_family": "warehouse_logistics", "notes": "Local metadata only"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "My Logistics Template" in response.text
    assert "Metadata only" in response.text
