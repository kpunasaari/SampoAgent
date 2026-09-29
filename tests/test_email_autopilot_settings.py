from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from urllib.parse import unquote

from sampoagent.app.main import create_app
from sampoagent.integrations.email_oauth import encrypt_token_payload


def _configured_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app(database_path=tmp_path / "email-autopilot.db")
    repository = app.state.repository
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_target_occupation("Cleaner", "Siivooja")
    return app, TestClient(app)


def _autopilot_payload(**overrides):
    return {
        "application_mode": "autopilot",
        "daily_limit": "3",
        "ai_usage_mode": "minimal",
        "dry_run": "false",
        "autopilot_ack": "yes",
        "email_send_autopilot_ack": "yes",
    } | overrides


def test_email_autopilot_setting_requires_separate_send_account(tmp_path, monkeypatch):
    app, client = _configured_app(tmp_path, monkeypatch)

    response = client.post("/settings", data=_autopilot_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert "separate send-only" in unquote(response.headers["location"])
    assert app.state.repository.setting("dry_run") == "true"
    assert not app.state.repository.autopilot_authorized()
    assert not app.state.repository.email_send_autopilot_authorized()


def test_email_autopilot_is_an_additional_scoped_consent(tmp_path, monkeypatch):
    app, client = _configured_app(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.save_mail_send_connection(
        "gmail",
        encrypt_token_payload({"access_token": "synthetic-token", "refresh_token": "synthetic-refresh", "expires_at": 4_000_000_000}),
        "openid email https://www.googleapis.com/auth/gmail.send",
        subject="google-test-subject", sender_email="sender@gmail.test", address_status="verified",
    )

    page = client.get("/settings")
    assert "without per-job prompts" in page.text
    assert "I separately authorize automatic sending" in page.text
    response = client.post("/settings", data=_autopilot_payload(), follow_redirects=False)

    assert response.status_code == 303
    assert repository.autopilot_authorized()
    assert repository.email_send_autopilot_authorized()

    repository.remove_mail_send_connection()
    assert not repository.email_send_autopilot_authorized()


def test_settings_unchecking_separate_mail_consent_revokes_it(tmp_path, monkeypatch):
    app, client = _configured_app(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.save_mail_send_connection(
        "gmail",
        encrypt_token_payload({"access_token": "synthetic-token", "refresh_token": "synthetic-refresh", "expires_at": 4_000_000_000}),
        "openid email https://www.googleapis.com/auth/gmail.send",
        subject="google-test-subject", sender_email="sender@gmail.test", address_status="verified",
    )
    client.post("/settings", data=_autopilot_payload())
    assert repository.email_send_autopilot_authorized()

    client.post("/settings", data=_autopilot_payload(email_send_autopilot_ack="no"))

    assert repository.autopilot_authorized()
    assert not repository.email_send_autopilot_authorized()
