from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_candidate_can_verify_a_live_listing_after_reviewing_its_destination():
    app = create_app(database_path=":memory:", demo_data=True)
    client = TestClient(app)
    app.state.repository.connection.execute(
        "UPDATE jobs SET application_url=? WHERE id=1",
        ("https://careers.northstar-logistics.fi/apply/warehouse",),
    )
    app.state.repository.connection.commit()

    page = client.get("/jobs")
    assert "I reviewed the live posting" in page.text
    response = client.post("/jobs/1/verify", data={"reviewed_current": "yes"}, follow_redirects=False)

    assert response.status_code == 303
    assert app.state.repository.job(1)["verification_state"] == "VERIFIED"
    assert app.state.repository.job_verification(1)["method"] == "user_reviewed_listing"


def test_candidate_cannot_verify_without_explicit_attestation():
    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    assert client.post("/jobs/1/verify", data={"reviewed_current": "no"}, follow_redirects=False).status_code == 303
    assert client.app.state.repository.job(1)["verification_state"] == "PARTIALLY_VERIFIED"


def test_changed_listing_snapshot_is_shown_as_stale_and_can_be_reverified():
    app = create_app(database_path=":memory:", demo_data=True)
    client = TestClient(app)
    repository = app.state.repository
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=1", ("https://careers.northstar-logistics.fi/apply/warehouse",))
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=1", ("https://careers.changed-employer.fi/apply/warehouse",))
    repository.connection.commit()

    stale_page = client.get("/jobs")

    assert "RE-VERIFY REQUIRED" in stale_page.text
    assert f"action='/jobs/1/verify'" in stale_page.text
    response = client.post("/jobs/1/verify", data={"reviewed_current": "yes"}, follow_redirects=False)

    assert response.status_code == 303
    from sampoagent.applications.runner import _active_verified_job

    assert _active_verified_job(repository.job(1))
