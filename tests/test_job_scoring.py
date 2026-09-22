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
