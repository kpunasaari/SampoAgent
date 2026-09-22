def test_missing_explicit_hard_requirement_blocks_queueing() -> None:
    from sampoagent.jobs.matching import hard_requirement_failures

    failures = hard_requirement_failures(
        required=["B-ajokortti", "Finnish"],
        confirmed_facts=["Finnish", "forklift operation"],
    )

    assert failures == ["Missing mandatory requirement: B-ajokortti"]
