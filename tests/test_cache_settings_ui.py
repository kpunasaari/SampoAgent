def test_settings_can_clear_local_semantic_cache() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:")
    repository = app.state.repository
    repository.cache_put("demo-key", "demo-value")
    client = TestClient(app)

    response = client.post("/settings/cache/clear", follow_redirects=True)

    assert response.status_code == 200
    assert repository.cache_get("demo-key") is None
    assert "Semantic cache cleared" in response.text
