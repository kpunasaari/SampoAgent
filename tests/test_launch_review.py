import json

from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def _ready_client(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "launch-review.db"),
        storage_dir=tmp_path / "storage",
    )
    repository = app.state.repository
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_skill("cleaning")
    return app, TestClient(app)


def test_guided_review_summarizes_profile_cv_scope_permissions_and_stop_rules(tmp_path):
    app, client = _ready_client(tmp_path)
    cv_path = tmp_path / "storage" / "uploads" / "synthetic-cv.txt"
    cv_path.parent.mkdir(parents=True)
    cv_path.write_text("Synthetic CV", encoding="utf-8")
    app.state.repository.add_document(kind="uploaded_cv", path=str(cv_path), checksum="a" * 64)
    app.state.repository.save_preferences({"locations": "Vantaa", "employment_type": "full_time"})

    response = client.get("/onboarding/ready")

    assert response.status_code == 200
    assert "Profile and CV evidence" in response.text
    assert "1 uploaded CV" in response.text
    assert "cleaning" in response.text
    assert "Cleaner / Siivooja" in response.text
    assert "Vantaa" in response.text
    assert "Daily application limit: 0" in response.text
    assert "CAPTCHA" in response.text and "Unknown or conflicting required answer" in response.text
    assert "Full Autopilot authorization: Off" in response.text
    assert app.state.repository.setting("application_mode") == "review_everything"
    assert app.state.repository.setting("dry_run") == "true"
    assert app.state.repository.latest_discovery_run() is None


def test_saving_reviewed_scope_and_mode_never_enables_live_submission(tmp_path):
    app, client = _ready_client(tmp_path)

    response = client.post(
        "/onboarding/ready",
        data={
            "action": "save_review",
            "selected_role": json.dumps(["Cleaner", "Siivooja"]),
            "locations": "Vantaa",
            "locations_exclude": "",
            "work_type": "onsite",
            "employment_type": "full_time",
            "schedule": "day",
            "search_terms_include": "school cleaning",
            "search_terms_exclude": "",
            "application_mode": "autopilot",
            "daily_limit": "3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/onboarding/ready?")
    active = [item for item in app.state.repository.target_occupations() if item["enabled"]]
    assert [(item["title_en"], item["title_fi"]) for item in active] == [("Cleaner", "Siivooja")]
    assert app.state.repository.preferences()["locations"] == "Vantaa"
    assert app.state.repository.preferences()["search_terms_include"] == "school cleaning"
    assert app.state.repository.setting("application_mode") == "autopilot"
    assert app.state.repository.setting("daily_limit") == "3"
    assert app.state.repository.setting("dry_run") == "true"
    assert app.state.repository.autopilot_authorized() is False


def test_guided_review_rejects_unknown_role_without_partially_changing_scope(tmp_path):
    app, client = _ready_client(tmp_path)

    response = client.post(
        "/onboarding/ready",
        data={
            "action": "save_review",
            "selected_role": json.dumps(["Invented Role", "Keksitty ammatti"]),
            "locations": "Turku",
            "locations_exclude": "",
            "work_type": "any",
            "employment_type": "any",
            "schedule": "any",
            "search_terms_include": "",
            "search_terms_exclude": "",
            "application_mode": "review_everything",
            "daily_limit": "0",
        },
    )

    assert response.status_code == 422
    assert app.state.repository.target_occupations() == []
    assert app.state.repository.preferences() == {}
    assert app.state.repository.setting("dry_run") == "true"


def test_full_autopilot_is_enabled_only_by_explicit_live_and_scope_grants(tmp_path):
    app, client = _ready_client(tmp_path)
    base = {
        "action": "enable_mode",
        "selected_role": json.dumps(["Cleaner", "Siivooja"]),
        "locations": "Vantaa",
        "locations_exclude": "",
        "work_type": "onsite",
        "employment_type": "full_time",
        "schedule": "day",
        "search_terms_include": "",
        "search_terms_exclude": "",
        "application_mode": "autopilot",
        "daily_limit": "2",
        "live_ack": "yes",
    }

    missing_autopilot_ack = client.post("/onboarding/ready", data=base)
    assert missing_autopilot_ack.status_code == 422
    assert app.state.repository.target_occupations() == []
    assert app.state.repository.setting("dry_run") == "true"
    assert app.state.repository.autopilot_authorized() is False

    enabled = client.post(
        "/onboarding/ready",
        data=base | {"autopilot_ack": "yes"},
        follow_redirects=False,
    )

    assert enabled.status_code == 303
    assert app.state.repository.setting("application_mode") == "autopilot"
    assert app.state.repository.setting("dry_run") == "false"
    assert app.state.repository.autopilot_authorized() is True
    assert app.state.automation_worker is None

    reviewed_again = client.get("/onboarding/ready")
    assert "Full Autopilot authorization: Active" in reviewed_again.text
    consent_control = reviewed_again.text.split("name='autopilot_ack'", 1)[1].split(">", 1)[0]
    assert "checked" not in consent_control
