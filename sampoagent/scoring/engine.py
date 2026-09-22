"""Pure, explainable score calculation for job opportunities."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionConfig:
    enabled: bool
    weight: int
    minimum: int


@dataclass(frozen=True)
class ScoreResult:
    final_score: float | None
    hard_blocked: bool
    hard_failures: list[str]
    queue_eligible: bool
    normalized_weights: dict[str, float]
    explanations: list[str]


def evaluate_score(
    *,
    eligibility: float,
    competitive_strength: float,
    confidence: float,
    configs: dict[str, DimensionConfig],
    hard_failures: list[str],
    queue_threshold: float = 60,
) -> ScoreResult:
    """Evaluate configured dimensions without allowing hard blockers through."""
    if hard_failures:
        return ScoreResult(None, True, hard_failures, False, {key: 0.0 for key in configs}, ["Hard requirement failed"])

    values = {
        "eligibility": eligibility,
        "competitive_strength": competitive_strength,
        "confidence": confidence,
    }
    enabled = {key: config for key, config in configs.items() if config.enabled}
    total_weight = sum(config.weight for config in enabled.values())
    normalized_weights = {key: round((config.weight / total_weight * 100) if config.enabled and total_weight else 0.0, 2) for key, config in configs.items()}
    final_score = sum(
        values[key] * config.weight / total_weight for key, config in enabled.items()
    ) if total_weight else 0.0
    meets_minimums = all(values[key] >= config.minimum for key, config in enabled.items())
    explanations = [f"{key.replace('_', ' ').title()} {'passed' if values[key] >= config.minimum else 'is below its minimum'}" for key, config in enabled.items()]
    return ScoreResult(final_score, False, [], meets_minimums and final_score >= queue_threshold, normalized_weights, explanations)
