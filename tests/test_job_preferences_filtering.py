def test_jobs_page_filters_by_saved_location_preferences() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    client.post(
        "/settings",
        data={
            "application_mode": "review_everything",
            "daily_limit": "5",
            "ai_usage_mode": "minimal",
            "locations": "Tampere",
            "work_type": "any",
            "keywords": "",
            "salary_minimum": "0",
        },
    )

    response = client.get("/jobs")

    assert "No jobs match your current preferences." in response.text
    assert "Warehouse Worker</td>" not in response.text


def test_jobs_page_filters_known_low_salary_and_discloses_uncomparable_pay(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app
    from sampoagent.jobs.service import normalize_job

    app = create_app(database_path=tmp_path / "salary-filter.db")
    repository = app.state.repository
    client = TestClient(app)
    repository.save_preferences({"salary_minimum": 2500})
    for title, description in (
        ("Low paid cleaner", "Salary €2,100–€2,300 per month."),
        ("Comparable cleaner", "Salary €2,500–€2,800 monthly."),
        ("Unstated cleaner", "Competitive pay; details discussed at interview."),
    ):
        job = normalize_job(
            title=title,
            company="Example Oy",
            location="Vantaa",
            description=description,
            application_url=f"https://careers.example.org/{title.lower().replace(' ', '-')}",
        )
        repository.add_job(job, "PARTIALLY_VERIFIED")

    response = client.get("/jobs")

    assert "Low paid cleaner" not in response.text
    assert "Comparable cleaner" in response.text
    assert "€2,500–€2,800 / month" in response.text
    assert "Unstated cleaner" in response.text
    assert "Not stated or not comparable" in response.text
