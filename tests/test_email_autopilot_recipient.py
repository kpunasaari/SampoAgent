from cryptography.fernet import Fernet

from sampoagent.app.main import create_app
from sampoagent.integrations.email_oauth import encrypt_token_payload, decrypt_token_payload
from sampoagent.integrations.email_send import EmailSendResult
from sampoagent.jobs.service import normalize_job


def _ready_email_application(tmp_path, monkeypatch, description):
    monkeypatch.setenv("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    app = create_app(database_path=tmp_path / "autopilot-email.db", storage_dir=tmp_path / "store")
    repository = app.state.repository
    repository.save_profile("Aino Example", "en", "aino@example.test")
    repository.add_target_occupation("Cleaner", "Siivooja")
    job_id = repository.add_job(normalize_job(
        title="Cleaner", company="Northstar Services", location="Vantaa",
        description=description,
        application_url="https://careers.northstar-logistics.fi/jobs/cleaner",
    ), "PARTIALLY_VERIFIED")
    repository.mark_job_user_reviewed(job_id, reviewed_current=True)
    application_id = repository.queue_application(job_id, language="en", cv_path=None)
    cv_path = tmp_path / "store" / "applications" / str(application_id) / "candidate.pdf"
    cv_path.parent.mkdir(parents=True)
    cv_path.write_bytes(b"%PDF-1.4\nSynthetic CV\n%%EOF")
    repository.update_application_cv_path(application_id, str(cv_path))
    repository.save_mail_send_connection(
        "gmail",
        encrypt_token_payload({"access_token": "synthetic-token", "refresh_token": "synthetic-refresh", "expires_at": 4_000_000_000}),
        "openid email https://www.googleapis.com/auth/gmail.send",
        subject="google-test-subject", sender_email="sender@gmail.test", address_status="verified",
    )
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("dry_run", "false")
    repository.set_setting("daily_limit", "3")
    repository.grant_autopilot(days=14)
    repository.grant_email_send_autopilot(days=14)
    return app, repository, application_id


def test_worker_builds_and_sends_verified_listing_email_without_browser_or_per_job_prompt(tmp_path, monkeypatch):
    from sampoagent.applications import worker

    app, repository, application_id = _ready_email_application(
        tmp_path, monkeypatch, "Apply by email to recruitment@northstar-logistics.fi."
    )
    browser_calls = []
    monkeypatch.setattr(worker, "process_application", lambda *_args: browser_calls.append("browser"))
    provider_calls = []

    def fake_send(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return EmailSendResult("ACCEPTED", "synthetic-ref", "Provider accepted; delivery unconfirmed.")

    report = worker.run_worker_cycle(
        repository, type("NoBrowser", (), {"configured": False})(), discover=False,
        storage_dir=app.state.storage_dir, email_sender=fake_send,
    )

    assert browser_calls == []
    assert len(provider_calls) == 1
    assert provider_calls[0][1]["recipient"] == "recruitment@northstar-logistics.fi"
    assert report.results == ((application_id, "EMAIL_ACCEPTED"),)
    package = decrypt_token_payload(repository.email_outbox_payload_ciphertext(application_id))
    assert package["sender_email"] == "sender@gmail.test"
    assert package["recipient_source"] == "verified_listing"
    assert package["recipient_cue_id"] == "en_apply_by_email"
    assert package["verified_snapshot_hash"]
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"


def test_ambiguous_email_route_is_held_and_other_supported_jobs_continue(tmp_path, monkeypatch):
    from sampoagent.applications import worker

    app, repository, email_application_id = _ready_email_application(
        tmp_path, monkeypatch, "Apply by email to one@northstar-logistics.fi or two@northstar-logistics.fi."
    )
    normal_job_id = repository.add_job(normalize_job(
        title="Cleaner", company="Other Services", location="Vantaa",
        description="Apply through the company form.",
        application_url="https://careers.other-services.fi/jobs/cleaner",
    ), "PARTIALLY_VERIFIED")
    repository.mark_job_user_reviewed(normal_job_id, reviewed_current=True)
    normal_application_id = repository.queue_application(normal_job_id, language="en", cv_path=None)
    browser_calls = []
    monkeypatch.setattr(worker, "process_application", lambda _repository, application_id, _browser: (browser_calls.append(application_id) or "SUBMITTED"))
    provider_calls = []

    report = worker.run_worker_cycle(
        repository, type("SyntheticBrowser", (), {"configured": True})(), discover=False,
        storage_dir=app.state.storage_dir,
        email_sender=lambda *_args, **_kwargs: (provider_calls.append("send") or EmailSendResult("ACCEPTED")),
    )

    assert provider_calls == []
    assert repository.application(email_application_id)["status"] == "NEEDS_REVIEW"
    assert repository.application(email_application_id)["queue_state"] == "WAITING_USER"
    assert "AMBIGUOUS_RECIPIENT" in repository.application(email_application_id)["notes"]
    assert browser_calls == [normal_application_id]
    assert report.results == ((email_application_id, "EMAIL_HELD"), (normal_application_id, "SUBMITTED"))


def test_email_route_without_separate_email_consent_is_held_not_browser_submitted(tmp_path, monkeypatch):
    from sampoagent.applications import worker

    app, repository, application_id = _ready_email_application(
        tmp_path, monkeypatch, "Apply by email to recruitment@northstar-logistics.fi."
    )
    repository.connection.execute("UPDATE settings SET value='false' WHERE key='email_send_autopilot_authorized'")
    repository.connection.execute("UPDATE settings SET value='' WHERE key='email_send_autopilot_fingerprint'")
    repository.connection.commit()
    browser_calls = []
    monkeypatch.setattr(worker, "process_application", lambda *_args: browser_calls.append("browser"))

    report = worker.run_worker_cycle(
        repository, type("SyntheticBrowser", (), {"configured": True})(), discover=False,
        storage_dir=app.state.storage_dir,
        email_sender=lambda *_args, **_kwargs: EmailSendResult("ACCEPTED"),
    )

    assert browser_calls == []
    assert report.results == ((application_id, "EMAIL_HELD"),)
    assert repository.email_outbox_for_application(application_id) is None
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"


def test_email_route_with_required_materials_not_in_template_is_held(tmp_path, monkeypatch):
    from sampoagent.applications import worker

    app, repository, application_id = _ready_email_application(
        tmp_path, monkeypatch,
        "Apply by email to recruitment@northstar-logistics.fi. Include a cover letter and your salary expectation.",
    )
    browser_calls = []
    provider_calls = []
    monkeypatch.setattr(worker, "process_application", lambda *_args: browser_calls.append("browser"))

    report = worker.run_worker_cycle(
        repository, type("SyntheticBrowser", (), {"configured": True})(), discover=False,
        storage_dir=app.state.storage_dir,
        email_sender=lambda *_args, **_kwargs: (provider_calls.append("send") or EmailSendResult("ACCEPTED")),
    )

    assert browser_calls == []
    assert provider_calls == []
    assert report.results == ((application_id, "EMAIL_HELD"),)
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    assert repository.application(application_id)["notes"].endswith("UNSUPPORTED_REQUIREMENTS.")
