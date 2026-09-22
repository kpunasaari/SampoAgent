def test_text_cv_upload_persists_reviewable_extracted_facts() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/cvs/upload",
        files={"file": ("candidate.txt", b"Skills: cleaning, forklift operation\nLanguages: Finnish, English", "text/plain")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "CV_EXTRACTED" in response.text
    assert "forklift operation" in response.text


def test_manual_job_import_normalizes_and_displays_explainable_score() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post(
        "/jobs/import",
        data={"title": "Warehouse Worker", "company": "Example Oy", "location": "Vantaa", "description": "Forklift operation required. Finnish required.", "application_url": "https://example.test/role/1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Warehouse Worker" in response.text
    assert "Eligibility" in response.text
