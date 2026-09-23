from sampoagent.jobs.discovery import build_search_plan


def test_search_plan_uses_confirmed_profile_targets_and_locations() -> None:
    plan = build_search_plan(
        facts=[
            {"type": "skill", "value": "forklift operation", "confirmed": 1, "rejected": 0},
            {"type": "experience", "value": "warehouse picker", "confirmed": 1, "rejected": 0},
            {"type": "skill", "value": "nursing", "confirmed": 0, "rejected": 0},
            {"type": "skill", "value": "cleaning", "confirmed": 1, "rejected": 1},
        ],
        candidate_records={"experience": [{"title": "Night-shift packer", "details": ""}]},
        targets=[
            {"title_en": "Warehouse Worker", "title_fi": "Varastotyöntekijä", "enabled": 1},
            {"title_en": "Nurse", "title_fi": "Sairaanhoitaja", "enabled": 0},
        ],
        career_profiles=[{"name": "Customer Service", "enabled": 1}, {"name": "Cooking", "enabled": 0}],
        preferences={"locations": "Helsinki, Vantaa"},
        sources=[
            {"id": 3, "name": "Duunitori", "url": "https://duunitori.fi", "enabled": 1, "capability": "Browser search only"},
            {"id": 4, "name": "Disabled", "url": "https://example.test", "enabled": 0, "capability": "Browser search only"},
        ],
    )

    assert "Warehouse Worker" in plan.terms
    assert "Varastotyöntekijä" in plan.terms
    assert "Night-shift packer" in plan.terms
    assert "Customer Service" in plan.terms
    assert "Nurse" not in plan.terms
    assert "Cooking" not in plan.terms
    assert "nursing" not in plan.terms
    assert plan.locations == ("Helsinki", "Vantaa")
    assert all(query.source_id == 3 for query in plan.queries)
    assert {query.location for query in plan.queries} == {"Helsinki", "Vantaa"}


def test_search_plan_deduplicates_terms_and_limits_generated_queries() -> None:
    plan = build_search_plan(
        facts=[{"type": "skill", "value": "forklift operation", "confirmed": 1, "rejected": 0}],
        candidate_records={},
        targets=[{"title_en": "Warehouse Worker", "title_fi": "Varastotyöntekijä", "enabled": 1}],
        career_profiles=[],
        preferences={"locations": []},
        sources=[
            {"id": source_id, "name": f"Source {source_id}", "url": f"https://source{source_id}.test", "enabled": 1, "capability": "Browser search only"}
            for source_id in range(10)
        ],
        max_queries=7,
    )

    assert len(plan.terms) == len(set(term.casefold() for term in plan.terms))
    assert len(plan.queries) == 7
    assert all(query.location == "" for query in plan.queries)


def test_user_search_terms_can_be_added_or_excluded_without_changing_profile_facts() -> None:
    plan = build_search_plan(
        facts=[],
        candidate_records={},
        targets=[],
        career_profiles=[],
        preferences={
            "locations": "Helsinki",
            "search_terms_include": "Forklift operator, mail clerk",
            "search_terms_exclude": "mail clerk",
        },
        sources=[{"id": 1, "name": "Duunitori", "url": "https://duunitori.fi", "enabled": 1, "capability": "Browser search only"}],
    )

    assert plan.terms == ("Forklift operator",)


def test_browser_search_links_are_domain_scoped_and_encoded() -> None:
    plan = build_search_plan(
        facts=[],
        candidate_records={},
        targets=[{"title_en": "Warehouse Worker", "title_fi": "Varastotyöntekijä", "enabled": 1}],
        career_profiles=[],
        preferences={"locations": "Espoo & Vantaa"},
        sources=[{"id": 8, "name": "Duunitori", "url": "https://www.duunitori.fi/tyopaikat", "enabled": 1, "capability": "Browser search only"}],
    )

    assert plan.queries
    assert all(query.search_url.startswith("https://www.google.com/search?q=") for query in plan.queries)
    assert all("site%3Awww.duunitori.fi" in query.search_url for query in plan.queries)
    assert all("Espoo+%26+Vantaa" in query.search_url for query in plan.queries)


def test_discovery_runs_keep_a_source_snapshot_after_source_deletion() -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(":memory:")
    repository.initialize()
    source_id = int(repository.rows("job_sources")[0]["id"])
    run_id = repository.create_discovery_run(query_count=4)
    repository.record_discovery_source_result(
        run_id,
        source_id=source_id,
        source_name="Duunitori",
        source_url="https://duunitori.fi",
        capability="Browser search only",
        status="links_ready",
        jobs_found=0,
        imported_count=0,
        duplicates_count=0,
        message="4 profile-derived searches are ready.",
    )
    repository.complete_discovery_run(
        run_id,
        status="completed",
        jobs_found=0,
        imported_count=0,
        duplicates_count=0,
        summary="Search links generated.",
    )

    repository.connection.execute("DELETE FROM job_sources WHERE id=?", (source_id,))

    assert repository.latest_discovery_run()["status"] == "completed"
    assert repository.discovery_source_results(run_id)[0]["source_name"] == "Duunitori"
