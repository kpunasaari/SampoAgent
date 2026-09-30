from fastapi.testclient import TestClient
import re

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


def test_application_question_ui_escapes_prompt_and_requires_csrf_and_explicit_confirmation(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "application-question-ui.db"), follow_redirects=False)
    repository = client.app.state.repository
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.update_application_status(application_id, "NEEDS_USER", "A required answer is missing", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[{
            "field_id": "shift", "label": "<img src=x onerror=alert(1)> Which shift?", "description": "Select a shift.",
            "kind": "select", "options": ["Day", "Night"], "required": True, "risk": "LOW",
        }],
    )

    page = client.get("/applications")
    assert "&lt;img src=x onerror=alert(1)&gt;" in page.text
    assert "application-only" in page.text.casefold()
    assert "Day" in page.text and "Night" in page.text
    csrf = re.search(r"name='csrf_token' value='([a-f0-9]+)'", page.text).group(1)

    denied = client.post(
        f"/applications/{application_id}/questions/1",
        data={"value": "Day", "confirmed": "yes", "csrf_token": "invalid"},
    )
    assert denied.status_code == 303
    assert repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="b" * 64) == {}

    unconfirmed = client.post(
        f"/applications/{application_id}/questions/1",
        data={"value": "Day", "csrf_token": csrf},
    )
    assert unconfirmed.status_code == 303
    assert repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="b" * 64) == {}

    saved = client.post(
        f"/applications/{application_id}/questions/1",
        data={"value": "Day", "confirmed": "yes", "csrf_token": csrf},
    )
    assert saved.status_code == 303
    assert repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="b" * 64) == {"shift": "Day"}
    assert repository.application(application_id)["queue_state"] == "READY"

    answered_page = client.get("/applications")
    assert "value='Day' selected" in answered_page.text
    updated = client.post(
        f"/applications/{application_id}/questions/1",
        data={"value": "Night", "confirmed": "yes", "csrf_token": csrf},
    )
    assert updated.status_code == 303
    assert repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="b" * 64) == {"shift": "Night"}


def test_full_autopilot_keeps_required_answer_review_collapsed_until_requested(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "autopilot-held-answer.db"), follow_redirects=False)
    repository = client.app.state.repository
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.update_application_status(
        application_id, "NEEDS_USER", "Required candidate answer needs review", queue_state="WAITING_USER",
    )
    repository.set_setting("application_mode", "autopilot")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[{
            "field_id": "start_date", "label": "When can you start?", "kind": "date",
            "required": True, "risk": "LOW",
        }],
    )

    page = client.get("/applications")

    assert page.status_code == 200
    details = re.search(
        r"<details><summary>Open manual review if you want to answer this application's required field</summary>(.*?)</details>",
        page.text,
        re.DOTALL,
    )
    assert details
    assert "When can you start?" in details.group(1)
    assert "Full Autopilot does not ask per-job questions" in page.text


def test_application_question_ui_preserves_employer_validation_attributes(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "application-question-constraints.db"))
    repository = client.app.state.repository
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.update_application_status(application_id, "NEEDS_USER", "A required answer is missing", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[
            {
                "field_id": "salary", "label": "Salary expectation", "kind": "number", "options": [],
                "constraints": {"min": "1000", "max": "5000", "step": "100"},
                "required": True, "risk": "LOW",
            },
            {
                "field_id": "employee_code", "label": "Employee code", "kind": "text", "options": [],
                "constraints": {"pattern": "[A-Z]{2}[0-9]{4}"},
                "required": True, "risk": "LOW",
            },
            {
                "field_id": "half_step", "label": "Half-step value", "kind": "number", "options": [],
                "constraints": {"step": "1", "step_base": "0.5"},
                "required": True, "risk": "LOW",
            },
        ],
    )

    page = client.get("/applications")

    assert "type='number'" in page.text
    assert "min='1000'" in page.text
    assert "max='5000'" in page.text
    assert "step='100'" in page.text
    assert "pattern='[A-Z]{2}[0-9]{4}'" in page.text
    half_step_question = next(item for item in repository.application_questions(application_id) if item["field_id"] == "half_step")
    half_step_control = re.search(rf"<input id='application-question-{half_step_question['id']}'[^>]*>", page.text).group(0)
    assert "step=" not in half_step_control
