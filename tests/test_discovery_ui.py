from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_job_match_explanation_uses_confirmed_structured_work_history(tmp_path):
    from sampoagent.jobs.service import normalize_job

    app = create_app(database_path=tmp_path / "work-history-score.db")
    repository = app.state.repository
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_candidate_record(
        "experience", {"title": "School Cleaner", "details": "School cleaning"}
    )
    job = normalize_job(
        title="School Cleaner",
        company="Example Facilities Oy",
        location="Vantaa",
        description="School cleaning experience preferred.",
        application_url="https://example.test/jobs/school-cleaner",
    )
    repository.add_job(job, "PARTIALLY_VERIFIED")

    response = TestClient(app).get("/jobs")

    assert response.status_code == 200
    assert "Matched confirmed candidate records: experience — School Cleaner" in response.text


def test_jobs_page_requires_a_selected_role_before_search_and_keeps_manual_entry_secondary(tmp_path):
    app = create_app(database_path=tmp_path / "jobs-ui.db")
    app.state.repository.save_profile("Kai", "en")
    app.state.repository.add_confirmed_fact(fact_type="experience", value="Cleaner")

    response = TestClient(app).get("/jobs")

    assert response.status_code == 200
    assert "action='/jobs/discover'" not in response.text
    assert "Choose a target role to generate searches" in response.text
    assert 'href="/careers"' in response.text
    assert "Personalized search links" in response.text
    assert "Add a job manually" in response.text


def test_discovery_post_needs_no_manual_job_fields_and_never_fetches_browser_only(tmp_path):
    app = create_app(database_path=tmp_path / "jobs-run.db")
    app.state.repository.save_profile("Kai", "en")
    app.state.repository.add_confirmed_fact(fact_type="experience", value="Cleaner")

    response = TestClient(app).post("/jobs/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs?notice=")
    run = app.state.repository.latest_discovery_run()
    assert run["status"] == "no_search_terms"
    assert run["jobs_found"] == 0
    results = app.state.repository.discovery_source_results(int(run["id"]))
    assert results == []


def test_jobs_discovery_explains_why_no_search_was_started(tmp_path):
    app = create_app(database_path=tmp_path / "no-target-notice.db")
    app.state.repository.save_profile("Kai", "en")
    app.state.repository.add_confirmed_fact(fact_type="skill", value="cleaning")

    response = TestClient(app).post("/jobs/discover", follow_redirects=True)

    assert response.status_code == 200
    assert "Choose a target occupation or add an explicit search keyword before discovery." in response.text


def test_discovery_redirects_to_setup_without_profile(tmp_path):
    app = create_app(database_path=tmp_path / "needs-setup.db")

    response = TestClient(app).post("/jobs/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"
    assert app.state.repository.latest_discovery_run() is None


def test_source_page_shows_last_check_and_persisted_retry_window(tmp_path):
    app = create_app(database_path=tmp_path / "source-health.db")
    repository = app.state.repository
    source_id = repository.add_source(
        name="Intermittent source",
        url="https://feeds.example.org/jobs",
        country="Finland",
        source_type="feed",
        capability="RSS/Atom feed",
    )
    run_id = repository.create_discovery_run(query_count=1)
    repository.record_discovery_source_result(
        run_id,
        source_id=source_id,
        source_name="Intermittent source",
        source_url="https://feeds.example.org/jobs",
        capability="RSS/Atom feed",
        status="failed",
        message="Source request timed out.",
    )

    response = TestClient(app).get("/sources")

    assert response.status_code == 200
    assert "failed" in response.text
    assert "last checked" in response.text
    assert "retry after" in response.text
