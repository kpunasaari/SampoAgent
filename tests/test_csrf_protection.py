from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_cross_origin_form_post_is_rejected_before_mutating_settings(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "csrf.db"))

    response = client.post(
        "/settings",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        data={"application_mode": "automatic", "daily_limit": "20"},
    )

    assert response.status_code == 403
    assert client.app.state.repository.setting("application_mode") == "review_everything"


def test_same_origin_and_legacy_non_browser_form_posts_are_allowed(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "csrf-allowed.db"), follow_redirects=False)

    response = client.post(
        "/settings",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        data={"application_mode": "review_everything", "daily_limit": "2", "ai_usage_mode": "minimal", "dry_run": "true"},
    )

    assert response.status_code == 303
    assert client.app.state.repository.setting("daily_limit") == "2"


def test_foreign_origin_is_rejected_even_on_sensitive_disconnect_route(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "csrf-disconnect.db"))
    repository = client.app.state.repository
    repository.save_mailbox_connection("gmail", "encrypted")

    response = client.post("/email/disconnect", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 403
    assert repository.mailbox_connection() is not None
