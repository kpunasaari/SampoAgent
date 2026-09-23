from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_jobs_page_has_one_click_profile_search_and_keeps_manual_entry_secondary(tmp_path):
    app = create_app(database_path=tmp_path / "jobs-ui.db")
    app.state.repository.save_profile("Kai", "en")
    app.state.repository.add_confirmed_fact(fact_type="experience", value="Cleaner")

    response = TestClient(app).get("/jobs")

    assert response.status_code == 200
    assert "action='/jobs/discover'" in response.text
    assert "Find matching jobs" in response.text
    assert "Personalized search links" in response.text
    assert "Cleaner" in response.text
    assert "Add a job manually" in response.text


def test_discovery_post_needs_no_manual_job_fields_and_never_fetches_browser_only(tmp_path):
    app = create_app(database_path=tmp_path / "jobs-run.db")
    app.state.repository.save_profile("Kai", "en")
    app.state.repository.add_confirmed_fact(fact_type="experience", value="Cleaner")

    response = TestClient(app).post("/jobs/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs?notice=")
    run = app.state.repository.latest_discovery_run()
    assert run["status"] == "completed"
    assert run["jobs_found"] == 0
    results = app.state.repository.discovery_source_results(int(run["id"]))
    assert len(results) == 13
    assert all(result["status"] == "browser_only" for result in results)


def test_discovery_redirects_to_setup_without_profile(tmp_path):
    app = create_app(database_path=tmp_path / "needs-setup.db")

    response = TestClient(app).post("/jobs/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"
    assert app.state.repository.latest_discovery_run() is None
