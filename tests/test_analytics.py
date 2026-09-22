from pathlib import Path


def test_analytics_use_stored_application_outcomes_not_fit_probability(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository
    from sampoagent.jobs.service import normalize_job

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    job = normalize_job(title="Cleaner", company="Example Oy", location="Helsinki", description="Cleaning", application_url="https://example.test/a")
    job_id = repository.add_job(job, "VERIFIED")
    application_id = repository.queue_application(job_id, language="fi", cv_path=None)
    repository.update_application_status(application_id, "INTERVIEW", "Interview invitation")

    metrics = repository.analytics()
    assert metrics["applications"] == 1
    assert metrics["interviews"] == 1
    assert metrics["interview_rate"] == 100.0
