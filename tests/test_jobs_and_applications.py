from datetime import date


def test_normalized_jobs_with_same_application_url_are_duplicates() -> None:
    from sampoagent.jobs.service import is_duplicate, normalize_job

    first = normalize_job(
        title="Varastotyöntekijä",
        company="North Logistics Oy",
        location="Vantaa",
        description="Etsimme trukkikokemusta.",
        application_url="https://example.test/jobs/42?source=one",
    )
    second = normalize_job(
        title="Warehouse worker",
        company="North Logistics Oy",
        location="Vantaa",
        description="Forklift experience required.",
        application_url="https://example.test/jobs/42?source=two",
    )

    assert is_duplicate(first, second) is True


def test_dry_run_and_high_risk_answers_never_submit() -> None:
    from sampoagent.applications.workflow import ApplicationMode, can_submit, classify_question

    assert classify_question("Do you have a criminal record?") == "HIGH"
    assert classify_question("What is your passport number?") == "HIGH"
    assert classify_question("What is your current notice period?") == "MEDIUM"
    assert classify_question("May we contact your previous employer?") == "LOW"
    assert classify_question("Do you have the legal right to work in Finland?") == "HIGH"
    assert classify_question("Onko sinulla rikostausta?") == "HIGH"
    assert classify_question("Har du lämnat in en hälsodeklaration?") == "HIGH"
    assert classify_question("Please confirm that you accept the privacy terms") == "HIGH"
    assert classify_question("What hourly pay do you expect?") == "MEDIUM"
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=True, risk="LOW", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=False, risk="HIGH", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.SMART_APPROVAL, dry_run=False, risk="MEDIUM", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=False, risk="LOW", applied_today=3, daily_limit=3) is False


def test_expired_deadline_is_not_verified() -> None:
    from sampoagent.jobs.service import verification_state

    assert verification_state(deadline=date(2020, 1, 1), employer="Example Oy", application_url="https://example.test") == "EXPIRED"


def test_manual_review_is_audited_and_expires_after_a_day(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sampoagent.applications.runner import _active_verified_job
    from sampoagent.db.repository import Repository
    from sampoagent.jobs.service import normalize_job

    repository = Repository(tmp_path / "reviewed-job.db")
    repository.initialize()
    job = normalize_job(
        title="Cleaner", company="North Employer Oy", location="Vantaa",
        description="Office cleaning", application_url="https://careers.north-employer.fi/jobs/42",
    )
    job_id = repository.add_job(job, "PARTIALLY_VERIFIED")

    repository.mark_job_user_reviewed(job_id, reviewed_current=True)

    saved = repository.job(job_id)
    assert saved["verification_state"] == "VERIFIED"
    assert repository.job_verification(job_id)["method"] == "user_reviewed_listing"
    assert _active_verified_job(saved)
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=?", ("https://careers.other-employer.fi/apply/42", job_id))
    repository.connection.commit()
    assert not _active_verified_job(repository.job(job_id))
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=?", ("https://north-employer.fi/jobs/42", job_id))
    repository.connection.commit()
    repository.connection.execute(
        "UPDATE jobs SET verified_at=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), job_id),
    )
    assert not _active_verified_job(repository.job(job_id))
