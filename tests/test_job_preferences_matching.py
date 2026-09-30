from sampoagent.jobs.matching import matches_preferences
from sampoagent.db.repository import Repository
from sampoagent.app.main import create_app
from sampoagent.jobs.service import normalize_job
from fastapi.testclient import TestClient


def _confirmed_profile_preferences():
    repository = Repository(":memory:")
    repository.initialize()
    repository.add_answer("PREFERENCE", "Preferred work locations", "Vantaa, Helsinki", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "On-site, hybrid or remote preferences", "On-site", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "Permanent, fixed-term, temporary or freelance preferences", "Permanent", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "Full-time / part-time preference and weekly hours", "Full-time", "USER_CONFIRMED")
    return repository


def test_confirmed_global_profile_preferences_flow_into_matching_without_activating_roles():
    repository = _confirmed_profile_preferences()

    preferences = repository.effective_search_preferences()

    assert preferences["locations"] == "Vantaa, Helsinki"
    assert preferences["work_type"] == "onsite"
    assert preferences["employment_type"] == "permanent"
    assert preferences["hours_type"] == "full_time"
    assert matches_preferences(
        job={
            "title": "Permanent full-time cleaner",
            "location": "Vantaa",
            "description": "On-site role, permanent, full-time 37.5 hours per week.",
        },
        preferences=preferences,
    )
    assert not matches_preferences(
        job={
            "title": "Temporary part-time cleaner",
            "location": "Turku",
            "description": "Remote, fixed-term, part-time role.",
        },
        preferences=preferences,
    )
    assert repository.has_explicit_search_scope() is False
    repository.connection.close()


def test_saved_search_settings_override_confirmed_profile_defaults_and_can_disable_them():
    repository = _confirmed_profile_preferences()
    repository.save_preferences({
        "locations": "Tampere", "work_type": "remote", "employment_type": "temporary", "hours_type": "part_time",
    })

    assert repository.effective_search_preferences() == {
        "locations": "Tampere", "work_type": "remote", "employment_type": "temporary", "hours_type": "part_time",
    }

    repository.save_preferences({"locations": "", "work_type": "any", "employment_type": "any", "hours_type": "any"})
    repository.set_setting("use_profile_preferences", "false")
    assert repository.effective_search_preferences()["locations"] == ""
    assert repository.effective_search_preferences()["work_type"] == "any"
    repository.connection.close()


def test_ambiguous_or_non_global_profile_answers_are_not_promoted_to_search_filters():
    repository = Repository(":memory:")
    repository.initialize()
    repository.add_answer("PREFERENCE", "Preferred work locations", "I can relocate anywhere except the far north", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "On-site, hybrid or remote preferences", "On-site or hybrid", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "Permanent, fixed-term, temporary or freelance preferences", "Permanent or fixed-term", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "Full-time / part-time preference and weekly hours", "Full-time or part-time", "USER_CONFIRMED")
    repository.add_answer(
        "PREFERENCE", "What is your expected salary?", "3000 EUR/month", "USER_CONFIRMED",
        scope_type="EMPLOYER", scope_employer="Northstar Oy",
    )
    repository.add_answer(
        "PREFERENCE", "Which job titles or fields interest you?", "Cleaner", "USER_CONFIRMED",
    )

    preferences = repository.effective_search_preferences()

    assert preferences.get("locations", "") == ""
    assert preferences.get("work_type", "any") == "any"
    assert preferences.get("employment_type", "any") == "any"
    assert preferences.get("hours_type", "any") == "any"
    assert repository.has_explicit_search_scope() is False
    repository.connection.close()


def test_generic_no_preference_location_text_does_not_become_a_location_filter():
    repository = Repository(":memory:")
    repository.initialize()
    repository.add_answer("PREFERENCE", "Preferred work locations", "Any location", "USER_CONFIRMED")

    assert repository.effective_search_preferences().get("locations", "") == ""
    repository.connection.close()


def test_jobs_page_uses_confirmed_profile_preferences_for_existing_listing_matches(tmp_path):
    app = create_app(database_path=tmp_path / "profile-preferences.db")
    repository = app.state.repository
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_answer("PREFERENCE", "Preferred work locations", "Vantaa", "USER_CONFIRMED")
    repository.add_answer("PREFERENCE", "On-site, hybrid or remote preferences", "On-site", "USER_CONFIRMED")
    for title, location, arrangement in (
        ("Vantaa Cleaner", "Vantaa", "On-site"),
        ("Turku Cleaner", "Turku", "Remote"),
    ):
        job = normalize_job(
            title=title,
            company="Synthetic Employer",
            location=location,
            description=f"Permanent full-time cleaning role. {arrangement} work.",
            application_url=f"https://jobs.example/{location.casefold()}",
        )
        repository.add_job(job, "PARTIALLY_VERIFIED")

    response = TestClient(app).get("/jobs")

    assert response.status_code == 200
    assert "Vantaa Cleaner" in response.text
    assert "Turku Cleaner" not in response.text


def test_explicit_include_and_exclude_filters_apply_without_inventing_missing_data():
    job = {"title": "Part-time Evening Cleaner", "company": "Example Oy", "location": "Vantaa", "description": "Hybrid work, evening shift.", "salary": None}
    assert matches_preferences(job=job, preferences={
        "locations": "Vantaa, Helsinki", "locations_exclude": "Tampere", "work_type": "hybrid",
        "employment_type": "part_time", "schedule": "evening", "title_include": "Cleaner",
        "title_exclude": "Manager", "employer_include": "Example", "employer_exclude": "Agency",
    })
    assert not matches_preferences(job=job, preferences={"title_exclude": "Cleaner"})
    assert not matches_preferences(job=job, preferences={"employer_include": "Other Oy"})
    assert not matches_preferences(job=job, preferences={"locations_exclude": "Vantaa"})


def test_profile_derived_work_filters_match_common_finnish_and_swedish_listing_terms():
    cases = (
        (
            {"title": "Tillsvidareanställd städare", "location": "Espoo", "description": "Heltid, distansarbete."},
            {"work_type": "remote", "employment_type": "permanent", "hours_type": "full_time"},
        ),
        (
            {"title": "Määräaikainen osa-aikainen siivooja", "location": "Vantaa", "description": "Lähityö."},
            {"work_type": "onsite", "employment_type": "temporary", "hours_type": "part_time"},
        ),
    )

    for job, preferences in cases:
        assert matches_preferences(job=job, preferences=preferences)


def test_opted_out_source_categories_are_excluded_from_existing_job_matches():
    preferences = {"include_public_sector": "no", "include_recruitment_agencies": "no"}

    assert not matches_preferences(
        job={"title": "Cleaner", "company": "City of Vantaa", "source_type": "public-sector board"},
        preferences=preferences,
    )
    assert not matches_preferences(
        job={"title": "Cleaner", "company": "Example Oy", "source_type": "Recruitment_Agency"},
        preferences=preferences,
    )
    assert matches_preferences(
        job={"title": "Cleaner", "company": "Example Oy", "source_type": "job board"},
        preferences=preferences,
    )
    assert not matches_preferences(
        job={"title": "Cleaner", "company": "Unknown source", "source_id": 91, "source_type": ""},
        preferences=preferences,
    )


def test_monthly_salary_floor_excludes_only_comparable_offers_below_the_floor():
    preferences = {"salary_minimum": 2500}

    assert not matches_preferences(
        job={"title": "Cleaner", "description": "Pay: €2,200–€2,400 per month."},
        preferences=preferences,
    )
    assert not matches_preferences(
        job={"title": "Cleaner", "description": "Lön EUR 2 300 per månad."},
        preferences=preferences,
    )
    assert matches_preferences(
        job={"title": "Cleaner", "description": "Salary 2 400–2 700 €/kk."},
        preferences=preferences,
    )
    assert matches_preferences(
        job={"title": "Cleaner", "description": "Hourly pay: €14.50 per hour."},
        preferences=preferences,
    )
    assert matches_preferences(
        job={"title": "Cleaner", "description": "Competitive salary; details discussed later."},
        preferences=preferences,
    )
    assert matches_preferences(
        job={"title": "Cleaner", "description": "Annual pay: EUR 28,000 per year."},
        preferences=preferences,
    )


def test_monthly_salary_qualifiers_are_not_misread_as_exact_pay():
    assert matches_preferences(
        job={"title": "Cleaner", "description": "Salary from EUR 2,400 per month."},
        preferences={"salary_minimum": 2500},
    )
    assert not matches_preferences(
        job={"title": "Cleaner", "description": "Salary up to EUR 2,400 per month."},
        preferences={"salary_minimum": 2500},
    )
