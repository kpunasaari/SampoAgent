from urllib.parse import parse_qs, urlsplit
import re
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.integrations.email_oauth import encrypt_token_payload
from sampoagent.integrations.email_oauth import decrypt_token_payload
from sampoagent.jobs.service import normalize_job


def _configure_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "local-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "local-client-secret")
    monkeypatch.setenv("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_send_oauth_requests_send_only_scope_and_keeps_read_connection_separate(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    import sampoagent.app.main as main

    exchange_calls = []

    def exchange(config, *, code, verifier):
        exchange_calls.append((config.provider, code))
        return {
            "access_token": f"access-{code}",
            "refresh_token": f"refresh-{code}",
            "scope": "https://www.googleapis.com/auth/gmail.send",
            "expires_in": 3600,
        }

    monkeypatch.setattr(main, "exchange_code", exchange)
    app = create_app(database_path=tmp_path / "email-send-oauth.db")
    client = TestClient(app)

    read_start = client.post("/email/connect/gmail", follow_redirects=False)
    read_params = parse_qs(urlsplit(read_start.headers["location"]).query)
    assert read_params["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    read_state = read_params["state"][0]
    client.get(f"/email/callback/gmail?code=read&state={read_state}", follow_redirects=False)
    read_ciphertext = app.state.repository.mailbox_ciphertext()

    send_start = client.post(
        "/email/send/connect/gmail",
        data={"csrf_token": _csrf_token(client.get("/settings/email"))},
        follow_redirects=False,
    )

    assert send_start.status_code == 303
    send_params = parse_qs(urlsplit(send_start.headers["location"]).query)
    assert send_params["scope"] == ["https://www.googleapis.com/auth/gmail.send"]
    send_state = send_params["state"][0]
    client.get(f"/email/callback/gmail?code=send&state={send_state}", follow_redirects=False)

    assert exchange_calls == [("gmail", "read"), ("gmail", "send")]
    assert app.state.repository.mailbox_ciphertext() == read_ciphertext
    send_connection = app.state.repository.mail_send_connection()
    assert send_connection["provider"] == "gmail"
    assert "gmail.send" in send_connection["granted_scopes"]
    assert "gmail.readonly" not in send_connection["granted_scopes"]


def test_send_oauth_cancel_does_not_create_or_replace_send_connection(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    app = create_app(database_path=tmp_path / "email-send-cancel.db")
    client = TestClient(app)

    start = client.post(
        "/email/send/connect/gmail",
        data={"csrf_token": _csrf_token(client.get("/settings/email"))},
        follow_redirects=False,
    )
    assert start.status_code == 303
    params = parse_qs(urlsplit(start.headers["location"]).query)
    state = params["state"][0]
    callback = client.get(f"/email/callback/gmail?error=access_denied&state={state}", follow_redirects=False)

    assert "cancelled" in parse_qs(urlsplit(callback.headers["location"]).query)["notice"][0]
    assert app.state.repository.mail_send_connection() is None


def test_send_permission_disconnect_requires_local_form_token(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    app = create_app(database_path=tmp_path / "email-send-disconnect.db")
    repository = app.state.repository
    repository.save_mail_send_connection(
        "gmail",
        encrypt_token_payload({"access_token": "synthetic", "refresh_token": "synthetic", "expires_at": 4_000_000_000}),
        "https://www.googleapis.com/auth/gmail.send",
    )
    client = TestClient(app)

    denied = client.post("/email/send/disconnect", data={"csrf_token": "invalid"}, follow_redirects=False)
    assert denied.status_code == 303
    assert repository.mail_send_connection() is not None

    token = _csrf_token(client.get("/settings/email"))
    allowed = client.post("/email/send/disconnect", data={"csrf_token": token}, follow_redirects=False)
    assert allowed.status_code == 303
    assert repository.mail_send_connection() is None


def _email_application(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    storage = tmp_path / "storage"
    app = create_app(database_path=tmp_path / "email-application.db", storage_dir=storage)
    repository = app.state.repository
    repository.save_profile("Aino Example", "en", "aino@example.test")
    job = normalize_job(
        title="Cleaner",
        company="Northstar Services",
        location="Vantaa",
        description="Apply by email to the address listed by the employer.",
        application_url="https://careers.northstar-logistics.fi/vacancy/cleaner",
    )
    job_id = repository.add_job(job, "PARTIALLY_VERIFIED")
    repository.mark_job_user_reviewed(job_id, reviewed_current=True)
    application_id = repository.queue_application(job_id, language="en", cv_path=None)
    cv_path = storage / "applications" / str(application_id) / "aino_cv.pdf"
    cv_path.parent.mkdir(parents=True)
    cv_path.write_bytes(b"%PDF-1.4\nsynthetic candidate CV\n%%EOF")
    repository.update_application_cv_path(application_id, str(cv_path))
    repository.save_mail_send_connection(
        "gmail",
        encrypt_token_payload({"access_token": "not-sent-in-this-test", "refresh_token": "local-test-token", "expires_at": 4_000_000_000}),
        "https://www.googleapis.com/auth/gmail.send",
    )
    return app, TestClient(app), application_id


def _csrf_token(page):
    match = re.search(r"name=['\"]csrf_token['\"] value=['\"]([a-f0-9]+)['\"]", page.text)
    assert match
    return match.group(1)


def test_application_email_draft_is_encrypted_unique_and_does_not_send(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)

    response = client.post(
        f"/email/applications/{application_id}/draft",
        data={"recipient": "recruitment@northstar.example", "confirm_recipient_from_posting": "yes", "csrf_token": _csrf_token(client.get("/applications"))},
        follow_redirects=False,
    )

    assert response.status_code == 303
    outbox = app.state.repository.email_outbox_for_application(application_id)
    assert outbox["state"] == "READY"
    assert len(outbox["package_hash"]) == 64
    assert "recruitment@northstar.example" not in str(outbox)
    assert "Aino Example" not in str(outbox)
    assert app.state.repository.application(application_id)["status"] == "QUEUED"
    assert app.state.repository.application(application_id)["queue_state"] == "EMAIL_READY"
    assert app.state.repository.ready_applications() == []
    stored_package = decrypt_token_payload(
        app.state.repository.email_outbox_payload_ciphertext(application_id)
    )
    assert stored_package["recipient"] == "recruitment@northstar.example"
    assert stored_package["attachment_sha256"]
    assert "Aino Example" in stored_package["body"]

    duplicate = client.post(
        f"/email/applications/{application_id}/draft",
        data={"recipient": "recruitment@northstar.example", "confirm_recipient_from_posting": "yes", "csrf_token": _csrf_token(client.get("/applications"))},
        follow_redirects=False,
    )
    assert duplicate.status_code == 303
    assert len(app.state.repository.email_outbox_items()) == 1


def test_email_draft_requires_candidate_confirmation_of_posting_recipient(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)

    response = client.post(
        f"/email/applications/{application_id}/draft",
        data={"recipient": "recruitment@northstar.example", "csrf_token": _csrf_token(client.get("/applications"))},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert app.state.repository.email_outbox_for_application(application_id) is None


def test_email_draft_form_is_clear_that_it_only_prepares_local_encrypted_content(tmp_path, monkeypatch):
    _configure_google(monkeypatch)
    app = create_app(database_path=tmp_path / "email-form.db")
    page = TestClient(app).get("/settings/email")

    assert page.status_code == 200
    assert "send-only OAuth grant" in page.text
    assert "are not sent" in page.text


def _create_draft(client, application_id):
    response = client.post(
        f"/email/applications/{application_id}/draft",
        data={"recipient": "recruitment@northstar.example", "confirm_recipient_from_posting": "yes", "csrf_token": _csrf_token(client.get("/applications"))},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_confirmed_exact_email_is_sent_once_and_provider_acceptance_is_not_delivery(tmp_path, monkeypatch):
    from sampoagent.integrations.email_send import EmailSendResult

    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "1")
    _create_draft(client, application_id)
    outbox = repository.email_outbox_for_application(application_id)
    calls = []

    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return EmailSendResult("ACCEPTED", "test-provider-id", "Provider accepted; delivery not confirmed.")

    import sampoagent.app.main as main
    monkeypatch.setattr(main, "send_email_message", send)
    page = client.get("/applications")
    csrf_token = _csrf_token(page)
    assert "recruitment@northstar.example" in page.text
    assert "aino_cv.pdf" in page.text

    response = client.post(
        f"/email/applications/{application_id}/send",
        data={"package_hash": outbox["package_hash"], "send_confirmation": "yes", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(calls) == 1
    assert repository.email_outbox_for_application(application_id)["state"] == "ACCEPTED"
    assert repository.email_outbox_for_application(application_id)["provider_reference"] == "test-provider-id"
    assert repository.application(application_id)["status"] == "EMAIL_ACCEPTED"
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"

    duplicate = client.post(
        f"/email/applications/{application_id}/send",
        data={"package_hash": outbox["package_hash"], "send_confirmation": "yes", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert duplicate.status_code == 303
    assert len(calls) == 1


def test_email_timeout_becomes_unknown_and_is_never_replayed(tmp_path, monkeypatch):
    from sampoagent.integrations.email_send import EmailSendResult

    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "5")
    _create_draft(client, application_id)
    outbox = repository.email_outbox_for_application(application_id)
    calls = []
    import sampoagent.app.main as main
    monkeypatch.setattr(main, "send_email_message", lambda *_a, **_k: (calls.append("one request") or EmailSendResult("UNKNOWN", message="Ambiguous provider result; no retry.")))
    csrf_token = _csrf_token(client.get("/applications"))
    form = {"package_hash": outbox["package_hash"], "send_confirmation": "yes", "csrf_token": csrf_token}

    client.post(f"/email/applications/{application_id}/send", data=form, follow_redirects=False)
    client.post(f"/email/applications/{application_id}/send", data=form, follow_redirects=False)

    assert calls == ["one request"]
    assert repository.email_outbox_for_application(application_id)["state"] == "UNKNOWN"
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"


def test_email_send_rechecks_cv_and_policy_before_any_provider_call(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "5")
    _create_draft(client, application_id)
    outbox = repository.email_outbox_for_application(application_id)
    application = repository.application(application_id)
    Path(application["cv_path"]).write_bytes(b"%PDF-1.4\nchanged\n%%EOF")
    calls = []
    import sampoagent.app.main as main
    monkeypatch.setattr(main, "send_email_message", lambda *_a, **_k: calls.append("sent"))
    csrf_token = _csrf_token(client.get("/applications"))

    client.post(
        f"/email/applications/{application_id}/send",
        data={"package_hash": outbox["package_hash"], "send_confirmation": "yes", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert not calls
    assert repository.email_outbox_for_application(application_id)["state"] == "READY"


def test_email_outbox_recovers_stale_sending_as_unknown_and_never_releases_slot(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "1")
    _create_draft(client, application_id)
    outbox = repository.email_outbox_for_application(application_id)
    claimed = repository.claim_email_outbox(
        application_id,
        package_hash=outbox["package_hash"],
        daily_limit=1,
        expected_scope_fingerprint=repository.automation_scope_fingerprint(),
    )
    assert claimed
    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    repository.connection.execute("UPDATE email_outbox SET started_at=? WHERE application_id=?", (stale, application_id))
    repository.connection.commit()

    assert repository.recover_interrupted_email_sends() == 1
    assert repository.email_outbox_for_application(application_id)["state"] == "UNKNOWN"
    assert repository.application(application_id)["status"] == "EMAIL_SUBMITTED_UNVERIFIED"
    assert repository.submissions_reserved_today() == 1
    second_job_id = repository.add_job(normalize_job(
        title="Warehouse Cleaner",
        company="Northstar Logistics",
        location="Vantaa",
        description="Apply through the employer website.",
        application_url="https://careers.northstar-logistics.fi/vacancy/warehouse-cleaner",
    ), "PARTIALLY_VERIFIED")
    second_application_id = repository.queue_application(second_job_id, language="en", cv_path=None)
    assert repository.claim_application_preparation(second_application_id, owner_token="worker-test")
    assert repository.claim_email_outbox(
        application_id,
        package_hash=outbox["package_hash"],
        daily_limit=1,
        expected_scope_fingerprint=repository.automation_scope_fingerprint(),
    ) is None
    assert repository.reserve_submission_attempt(
        second_application_id,
        daily_limit=1,
        package_hash="f" * 64,
        preparation_token="worker-test",
    ) is None


def test_cancelled_unsent_email_draft_can_be_replaced_but_not_sent_without_csrf(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)
    _create_draft(client, application_id)
    repository = app.state.repository
    first_hash = repository.email_outbox_for_application(application_id)["package_hash"]
    csrf_token = _csrf_token(client.get("/applications"))

    rejected = client.post(
        f"/email/applications/{application_id}/cancel",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert repository.email_outbox_for_application(application_id)["state"] == "READY"

    cancelled = client.post(
        f"/email/applications/{application_id}/cancel",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert repository.email_outbox_for_application(application_id)["state"] == "CANCELLED"

    replacement = client.post(
        f"/email/applications/{application_id}/draft",
        data={"recipient": "talent@northstar.example", "confirm_recipient_from_posting": "yes", "csrf_token": _csrf_token(client.get("/applications"))},
        follow_redirects=False,
    )
    assert replacement.status_code == 303
    assert repository.email_outbox_for_application(application_id)["state"] == "READY"
    assert repository.email_outbox_for_application(application_id)["package_hash"] != first_hash


def _enable_email_autopilot(repository):
    repository.add_target_occupation("Cleaner", "Siivooja")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("dry_run", "false")
    repository.set_setting("automation_paused", "false")
    repository.set_setting("daily_limit", "3")
    repository.grant_autopilot(days=14)


def test_worker_leaves_email_draft_queued_without_separate_email_autopilot_grant(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    _enable_email_autopilot(repository)
    _create_draft(client, application_id)
    attempts = []
    import sampoagent.applications.worker as worker
    monkeypatch.setattr(worker, "send_email_message", lambda *_a, **_k: attempts.append("send"))

    report = worker.run_worker_cycle(
        repository,
        type("NoBrowser", (), {"configured": False})(),
        discover=False,
        storage_dir=app.state.storage_dir,
    )

    assert report.status == "completed"
    assert attempts == []
    assert repository.email_outbox_for_application(application_id)["state"] == "READY"
    assert repository.application(application_id)["queue_state"] == "EMAIL_READY"


def test_email_autopilot_worker_sends_candidate_confirmed_draft_only_with_separate_grant(tmp_path, monkeypatch):
    from sampoagent.integrations.email_send import EmailSendResult

    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    _enable_email_autopilot(repository)
    _create_draft(client, application_id)
    repository.grant_email_send_autopilot(days=14)
    calls = []
    import sampoagent.applications.worker as worker

    def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        return EmailSendResult("ACCEPTED", "synthetic-ref", "Provider accepted; delivery unconfirmed.")

    monkeypatch.setattr(worker, "send_email_message", fake_send)
    report = worker.run_worker_cycle(
        repository,
        type("NoBrowser", (), {"configured": False})(),
        discover=False,
        storage_dir=app.state.storage_dir,
    )

    assert len(calls) == 1
    assert calls[0][1]["recipient"] == "recruitment@northstar.example"
    assert report.results == ((application_id, "EMAIL_ACCEPTED"),)
    assert repository.email_outbox_for_application(application_id)["state"] == "ACCEPTED"
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"


def test_email_autopilot_requires_separate_grant_and_is_bound_to_scope_and_account(tmp_path, monkeypatch):
    app, client, application_id = _email_application(tmp_path, monkeypatch)
    repository = app.state.repository
    assert not repository.email_send_autopilot_authorized()
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "3")
    repository.add_target_occupation("Cleaner", "Siivooja")
    repository.grant_autopilot(days=14)

    repository.grant_email_send_autopilot()
    assert repository.email_send_autopilot_authorized()
    repository.save_preferences({**repository.preferences(), "locations": "Vantaa"})
    assert not repository.email_send_autopilot_authorized()
