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
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=True, risk="LOW", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=False, risk="HIGH", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.SMART_APPROVAL, dry_run=False, risk="MEDIUM", applied_today=0, daily_limit=3) is False
    assert can_submit(ApplicationMode.AUTOPILOT, dry_run=False, risk="LOW", applied_today=3, daily_limit=3) is False


def test_expired_deadline_is_not_verified() -> None:
    from sampoagent.jobs.service import verification_state

    assert verification_state(deadline=date(2020, 1, 1), employer="Example Oy", application_url="https://example.test") == "EXPIRED"
