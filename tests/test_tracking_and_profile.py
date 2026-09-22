from pathlib import Path


def test_facts_can_be_confirmed_edited_and_rejected(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    fact_id = repository.add_extracted_fact(fact_type="skill", value="forklift", source_id="cv.txt", confidence=0.8)
    repository.confirm_fact(fact_id)
    repository.edit_fact(fact_id, "forklift operation")
    assert repository.rows("facts")[0]["value"] == "forklift operation"
    assert repository.rows("facts")[0]["confirmed"] == 1
    repository.reject_fact(fact_id)
    assert repository.rows("facts")[0]["rejected"] == 1


def test_application_tracking_preserves_status_timeline_and_daily_limit(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository
    from sampoagent.jobs.service import normalize_job

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    job = normalize_job(title="Cleaner", company="Example Oy", location="Helsinki", description="Cleaning", application_url="https://example.test/a")
    job_id = repository.add_job(job, "PARTIALLY_VERIFIED")
    application_id = repository.queue_application(job_id, language="en", cv_path="application_data/a/001_example.pdf")
    repository.update_application_status(application_id, "APPLIED", "Submitted manually")

    assert repository.application_timeline(application_id)[-1]["status"] == "APPLIED"
    assert repository.applications_today() == 1
    assert repository.can_queue_or_submit(daily_limit=1) is False
