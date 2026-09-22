def test_cv_page_can_generate_confirmed_fact_only_pdf() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Generated" in response.text
