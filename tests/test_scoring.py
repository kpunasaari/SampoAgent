def test_hard_requirement_failure_blocks_an_otherwise_strong_job() -> None:
    from sampoagent.scoring.engine import DimensionConfig, evaluate_score

    result = evaluate_score(
        eligibility=95,
        competitive_strength=95,
        confidence=95,
        configs={
            "eligibility": DimensionConfig(enabled=True, weight=50, minimum=60),
            "competitive_strength": DimensionConfig(enabled=True, weight=35, minimum=55),
            "confidence": DimensionConfig(enabled=True, weight=15, minimum=65),
        },
        hard_failures=["Mandatory nursing qualification is not confirmed"],
    )

    assert result.hard_blocked is True
    assert result.queue_eligible is False
    assert result.final_score is None
    assert result.hard_failures == ["Mandatory nursing qualification is not confirmed"]


def test_enabled_dimension_weights_are_normalized_and_thresholds_are_explained() -> None:
    from sampoagent.scoring.engine import DimensionConfig, evaluate_score

    result = evaluate_score(
        eligibility=70,
        competitive_strength=90,
        confidence=0,
        configs={
            "eligibility": DimensionConfig(enabled=True, weight=2, minimum=60),
            "competitive_strength": DimensionConfig(enabled=True, weight=1, minimum=80),
            "confidence": DimensionConfig(enabled=False, weight=99, minimum=100),
        },
        hard_failures=[],
    )

    assert result.normalized_weights == {"eligibility": 66.67, "competitive_strength": 33.33, "confidence": 0.0}
    assert result.queue_eligible is True
    assert "Eligibility passed" in result.explanations
