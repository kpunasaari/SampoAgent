def test_selected_job_prefills_its_language_for_cv_generation() -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    response = client.get("/cvs?job_id=2")

    assert response.status_code == 200
    assert "Generate for job: Example Services Oy — Asiakaspalvelija" in response.text
    assert "<option value='fi' selected>Finnish</option>" in response.text
    assert "name='job_id' value='2'" in response.text


def test_unknown_job_id_does_not_change_cv_language_selection() -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.get("/cvs?job_id=999")

    assert response.status_code == 200
    assert "Generate for job:" not in response.text
    assert "<option value='en' selected>English</option>" in response.text
