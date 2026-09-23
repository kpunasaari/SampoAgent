from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.integrations.email_oauth import encrypt_token_payload
from sampoagent.integrations.mailbox import MailboxMessage


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "local-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "local-client-secret")
    monkeypatch.setenv("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_email_settings_explains_setup_without_asking_for_raw_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    app = create_app(database_path=tmp_path / "email-setup.db")
    page = TestClient(app).get("/settings/email")
    assert page.status_code == 200
    assert "Connect Gmail" in page.text
    assert "Secrets are never entered in this page" in page.text
    assert 'name="client_secret"' not in page.text


def test_email_oauth_state_is_one_time_and_tokens_are_encrypted(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    import sampoagent.app.main as main

    monkeypatch.setattr(main, "exchange_code", lambda *args, **kwargs: {"access_token": "very-secret-token", "refresh_token": "refresh-secret", "expires_in": 3600})
    app = create_app(database_path=tmp_path / "oauth.db")
    client = TestClient(app)

    start = client.post("/email/connect/gmail", follow_redirects=False)
    params = parse_qs(urlsplit(start.headers["location"]).query)
    assert start.status_code == 303
    assert params["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    state = params["state"][0]

    callback = client.get(f"/email/callback/gmail?code=fake-code&state={state}", follow_redirects=False)
    assert callback.status_code == 303
    assert app.state.repository.mailbox_connection()["provider"] == "gmail"
    assert "very-secret-token" not in app.state.repository.mailbox_ciphertext()

    replay = client.get(f"/email/callback/gmail?code=again&state={state}", follow_redirects=False)
    assert "expired or did not match" in parse_qs(urlsplit(replay.headers["location"]).query)["notice"][0]


def test_oauth_cancel_requires_valid_one_time_state_and_stores_no_tokens(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    app = create_app(database_path=tmp_path / "oauth-cancel.db")
    client = TestClient(app)
    start = client.post("/email/connect/gmail", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/email/callback/gmail?error=access_denied&state={state}", follow_redirects=False)
    assert "cancelled" in parse_qs(urlsplit(callback.headers["location"]).query)["notice"][0]
    assert app.state.repository.mailbox_connection() is None


def test_mail_sync_stores_response_for_manual_review_then_user_confirms(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    import sampoagent.app.main as main

    monkeypatch.setattr(main, "fetch_recent_messages", lambda *args, **kwargs: [MailboxMessage("m-1", "hr@example.org", "Interview invitation", "Can we meet?", "today", "https://mail.google.com/mail/u/0/#all/m-1")])
    app = create_app(database_path=tmp_path / "email-sync.db")
    repository = app.state.repository
    repo_job = __import__("sampoagent.jobs.service", fromlist=["normalize_job"]).normalize_job(title="Cleaner", company="Example Oy", location="Vantaa", description="Cleaning", application_url="https://example.org/1")
    job_id = repository.add_job(repo_job, "PARTIALLY_VERIFIED")
    application_id = repository.queue_application(job_id, language="en", cv_path=None)
    repository.save_mailbox_connection("gmail", encrypt_token_payload({"access_token": "token", "refresh_token": "refresh", "expires_at": 9_999_999_999}))
    client = TestClient(app)

    synced = client.post("/email/sync", follow_redirects=False)
    assert "1 possible job replies" in parse_qs(urlsplit(synced.headers["location"]).query)["notice"][0]
    stored = repository.mailbox_messages(include_reviewed=False)
    assert len(stored) == 1
    assert repository.application(application_id)["status"] == "QUEUED"

    client.post(f"/email/messages/{stored[0]['id']}/confirm", data={"application_id": application_id, "status": "INTERVIEW"})
    assert repository.application(application_id)["status"] == "INTERVIEW"
    assert repository.mailbox_messages(include_reviewed=False) == []


def test_disconnect_removes_encrypted_tokens_and_synced_metadata(tmp_path):
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "disconnect.db")
    repository.initialize()
    repository.save_mailbox_connection("gmail", "encrypted-token-data")
    repository.remove_mailbox_connection()
    assert repository.mailbox_connection() is None
    assert repository.mailbox_ciphertext() is None
