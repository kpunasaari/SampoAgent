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
