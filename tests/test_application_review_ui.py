from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def _prepared_review(client: TestClient) -> tuple[int, str]:
    repository = client.app.state.repository
    repository.load_demo()
    repository.connection.execute(
        "UPDATE jobs SET application_url=? WHERE id=1",
        ("https://careers.example.fi/jobs/warehouse",),
    )
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    application_id = repository.queue_application(1, language="en", cv_path=None)
    package = {
        "application": application_id,
        "job": {
            "id": 1,
            "title": "Warehouse Worker",
            "company": "Northern Logistics Oy",
            "application_url": "https://careers.example.fi/jobs/warehouse",
        },
        "form": "form-signature",
        "answers": [
            {"field_id": "name", "label": "Full name", "value": "Aino Example", "source": "profile.name"}
        ],
        "cv": {"name": "Aino Example - Warehouse CV.pdf", "sha256": "a" * 64},
    }
    package_hash = "b" * 64
    repository.save_application_review(application_id, package_hash=package_hash, package=package)
    return application_id, package_hash


def test_application_page_shows_exact_package_and_approval_action(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "review-ui.db"), follow_redirects=False)
    application_id, package_hash = _prepared_review(client)

    response = client.get("/applications")

    assert response.status_code == 200
    assert "Awaiting your approval" in response.text
    assert "Aino Example" in response.text
    assert "Warehouse Worker" in response.text
    assert "Aino Example - Warehouse CV.pdf" in response.text
    assert "a" * 64 in response.text
    assert package_hash in response.text
    assert f"/applications/{application_id}/approve" in response.text


def test_approval_route_approves_only_the_matching_package_hash(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "approve-ui.db"), follow_redirects=False)
    application_id, package_hash = _prepared_review(client)

    response = client.post(
        f"/applications/{application_id}/approve",
        data={"package_hash": "c" * 64},
    )
    repository = client.app.state.repository
    assert response.status_code == 303
    assert repository.application_review(application_id)["state"] == "WAITING"
    assert repository.application(application_id)["status"] == "NEEDS_REVIEW"

    response = client.post(
        f"/applications/{application_id}/approve",
        data={"package_hash": package_hash},
    )

    assert response.status_code == 303
    assert repository.application_review(application_id)["state"] == "APPROVED"
    assert repository.application(application_id)["status"] == "QUEUED"
    assert repository.application(application_id)["queue_state"] == "READY"


def test_conflicting_candidate_answer_status_links_to_both_provenance_reviews(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "conflict-review.db"), follow_redirects=False)
    repository = client.app.state.repository
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.update_application_status(
        application_id,
        "NEEDS_USER",
        "Required or conflicting candidate answers need review: licence",
        queue_state="WAITING_USER",
    )

    response = client.get("/applications")

    assert response.status_code == 200
    assert "conflicting candidate answers" in response.text
    assert "Review CV facts" in response.text
    assert "href='/profile'" in response.text
    assert "Review questionnaire answers" in response.text
    assert "href='/answers'" in response.text
