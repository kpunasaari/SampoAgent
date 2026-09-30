from sampoagent.jobs.matching import matches_preferences


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
