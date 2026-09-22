from pathlib import Path

from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_profile_and_queue_keep_their_existing_form_contracts() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    profile = client.get("/profile")
    queue = client.get("/queue")

    assert "action='/profile/skills'" in profile.text
    assert "name='skill'" in profile.text
    assert "name='cv_path'" in queue.text
    assert 'class="table-wrap"' in profile.text


def test_answers_keep_explicit_risk_text() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/answers",
        data={
            "category": "FACT",
            "question": "What is your passport number?",
            "value": "Never store this",
            "source": "USER_CONFIRMED",
        },
    )

    page = client.get("/answers")

    assert "High-risk questions always require your intervention" in page.text
    assert '<span class="status status-risk">HIGH</span>' in page.text


def test_settings_groups_controls_without_changing_the_form_endpoint() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/settings")

    assert "class='settings-group'" in page.text
    assert "Application controls" in page.text
    assert "Job preferences" in page.text
    assert "Explainable scoring configuration" in page.text
    assert "action='/settings'" in page.text


def test_cv_page_keeps_generation_and_download_workflow_visible(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "agent.db")
    client = TestClient(app)

    page = client.get("/cvs?job_id=2")

    assert "Generate confirmed-fact CV" in page.text
    assert "action='/cvs/generate'" in page.text
    assert "Generate for job:" in page.text
