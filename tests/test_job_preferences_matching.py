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
