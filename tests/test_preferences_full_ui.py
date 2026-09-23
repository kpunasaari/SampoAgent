from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_settings_exposes_and_persists_search_and_review_preferences(tmp_path):
    app = create_app(database_path=tmp_path / "settings-ui.db")
    client = TestClient(app)
    response = client.get("/settings")

    for control in ("locations_exclude", "employment_type", "schedule", "search_terms_include", "search_terms_exclude", "title_include", "title_exclude", "industries", "employer_include", "employer_exclude", "radius_km", "include_public_sector", "include_recruitment_agencies"):
        assert f'name=\'{control}\'' in response.text

    saved = client.post("/settings", data={
        "application_mode": "review_everything", "daily_limit": 7, "ai_usage_mode": "minimal",
        "locations": "Vantaa, Helsinki", "locations_exclude": "Tampere", "work_type": "hybrid",
        "employment_type": "part_time", "schedule": "evening", "keywords": "cleaner",
        "search_terms_include": "English speaking", "search_terms_exclude": "commission only",
        "title_include": "Cleaner", "title_exclude": "Sales", "industries": "Facilities",
        "employer_include": "Example Oy", "employer_exclude": "Bad Employer", "salary_minimum": 1800,
        "radius_km": 30, "include_public_sector": "no", "include_recruitment_agencies": "yes",
    }, follow_redirects=False)

    assert saved.status_code == 303
    preferences = app.state.repository.preferences()
    assert preferences["locations"] == "Vantaa, Helsinki"
    assert preferences["employment_type"] == "part_time"
    assert preferences["search_terms_exclude"] == "commission only"
    assert preferences["include_public_sector"] == "no"
    assert preferences["radius_km"] == 30
    rendered = client.get("/settings").text
    assert "Vantaa, Helsinki" in rendered
    assert "commission only" in rendered


def test_explicit_source_category_preference_filters_the_search_plan():
    from sampoagent.jobs.discovery import build_search_plan

    plan = build_search_plan(
        facts=[{"type": "experience", "value": "Cleaner", "confirmed": 1, "rejected": 0}],
        candidate_records={}, targets=[], career_profiles=[],
        preferences={"include_public_sector": "no", "include_recruitment_agencies": "no"},
        sources=[
            {"id": 1, "name": "Board", "url": "https://board.fi", "source_type": "job board", "enabled": 1},
            {"id": 2, "name": "City", "url": "https://city.fi", "source_type": "public-sector board", "enabled": 1},
            {"id": 3, "name": "Agency", "url": "https://agency.fi", "source_type": "recruitment agency", "enabled": 1},
        ],
    )
    assert {query.source_id for query in plan.queries} == {1}
