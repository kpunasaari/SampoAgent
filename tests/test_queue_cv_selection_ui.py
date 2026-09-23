def test_queue_retains_selected_generated_cv_path() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True)
    client = TestClient(app)
    client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"})
    document = app.state.repository.documents(kind="generated_cv")[0]

    client.post("/queue/prepare/1", data={"cv_path": document["path"]})
    application = app.state.repository.application(1)

    assert application is not None
    assert application["cv_path"] == document["path"]
