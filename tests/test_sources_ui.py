def test_sources_page_exposes_finland_catalogue_and_adds_missing_builtins() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    initial = client.get("/sources")
    assert "Academic Work Finland" in initial.text
    assert "Add missing Finland sources" in initial.text

    populated = client.post("/sources/builtin", follow_redirects=True)
    assert "Jobly" in populated.text
    assert "Valtiolle" in populated.text
