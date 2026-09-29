def test_job_score_uses_confirmed_facts_and_verification() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={
            "title": "Warehouse Worker",
            "description": "Forklift operation and Finnish required.",
            "verification_state": "VERIFIED",
        },
        confirmed_facts=["forklift operation", "Finnish"],
    )

    assert result.hard_blocked is False
    assert result.final_score is not None
    assert result.final_score >= 70
    assert "Matched confirmed facts: forklift operation, Finnish" in result.explanations


def test_job_score_blocks_explicit_missing_finnish_requirement() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={
            "title": "Delivery Driver",
            "description": "B-ajokortti vaaditaan.",
            "verification_state": "VERIFIED",
        },
        confirmed_facts=["Finnish"],
    )

    assert result.hard_blocked is True
    assert result.final_score is None
    assert result.hard_failures == ["Missing mandatory requirement: B-ajokortti"]


def test_job_score_matches_confirmed_language_across_finnish_and_english() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={"title": "Asiakaspalvelija", "description": "Englanti on tarpeen.", "verification_state": "VERIFIED"},
        confirmed_facts=["English"],
    )

    assert "Matched confirmed facts: English" in result.explanations
    assert result.queue_eligible is True


def test_job_score_uses_confirmed_work_history_when_no_skill_fact_matches() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={
            "title": "School Cleaner",
            "description": "School cleaning experience preferred.",
            "verification_state": "VERIFIED",
        },
        confirmed_facts=[],
        confirmed_records={
            "experience": [{
                "review_state": "CONFIRMED",
                "title": "School Cleaner",
                "details": "School cleaning",
            }],
        },
    )

    assert result.queue_eligible is True
    assert result.final_score == 77.0
    assert "Matched confirmed candidate records: experience — School Cleaner" in result.explanations


def test_job_score_does_not_use_draft_candidate_records() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={
            "title": "School Cleaner",
            "description": "School cleaning experience preferred.",
            "verification_state": "VERIFIED",
        },
        confirmed_facts=[],
        confirmed_records={
            "experience": [{
                "review_state": "DRAFT",
                "title": "School Cleaner",
                "details": "School cleaning",
            }],
        },
    )

    assert result.final_score == 54.0
    assert result.queue_eligible is False
    assert "Matched confirmed candidate records: experience — School Cleaner" not in result.explanations


def test_confirmed_licence_record_can_satisfy_its_exact_mandatory_requirement() -> None:
    from sampoagent.scoring.job_score import evaluate_job

    result = evaluate_job(
        job={
            "title": "Delivery Driver",
            "description": "B-ajokortti vaaditaan.",
            "verification_state": "VERIFIED",
        },
        confirmed_facts=[],
        confirmed_records={
            "licence": [{"review_state": "CONFIRMED", "name": "B-ajokortti"}],
        },
    )

    assert result.hard_blocked is False
    assert result.queue_eligible is True
