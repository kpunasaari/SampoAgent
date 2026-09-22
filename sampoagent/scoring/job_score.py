"""Deterministic, job-specific scoring built from confirmed candidate facts."""

from collections.abc import Mapping
import json

from sampoagent.country_packs.finland.requirements import parse_requirements
from sampoagent.jobs.matching import hard_requirement_failures
from sampoagent.scoring.engine import DimensionConfig, ScoreResult, evaluate_score


DEFAULT_CONFIGS = {
    "eligibility": DimensionConfig(enabled=True, weight=50, minimum=60),
    "competitive_strength": DimensionConfig(enabled=True, weight=35, minimum=40),
    "confidence": DimensionConfig(enabled=True, weight=15, minimum=40),
}


def load_configs(serialized: str | None) -> dict[str, DimensionConfig]:
    """Load validated persisted score settings, falling back to safe defaults."""
    if not serialized:
        return dict(DEFAULT_CONFIGS)
    try:
        raw = json.loads(serialized)
        configs = {
            name: DimensionConfig(
                enabled=bool(raw[name]["enabled"]),
                weight=float(raw[name]["weight"]),
                minimum=float(raw[name]["minimum"]),
            )
            for name in DEFAULT_CONFIGS
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIGS)
    if not any(config.enabled and config.weight > 0 for config in configs.values()):
        return dict(DEFAULT_CONFIGS)
    return configs


def dump_configs(configs: Mapping[str, DimensionConfig]) -> str:
    return json.dumps(
        {
            name: {"enabled": config.enabled, "weight": config.weight, "minimum": config.minimum}
            for name, config in configs.items()
        },
        sort_keys=True,
    )

_CONFIDENCE_BY_VERIFICATION = {
    "VERIFIED": 100,
    "PARTIALLY_VERIFIED": 70,
    "UNVERIFIED": 40,
    "EXPIRED": 0,
}

_FACT_ALIASES = {
    "finnish": ("finnish", "suomi", "suomen kieli"),
    "english": ("english", "englanti", "englannin kieli"),
    "swedish": ("swedish", "ruotsi", "ruotsin kieli"),
}


def _fact_matches_job(fact: str, searchable: str) -> bool:
    aliases = _FACT_ALIASES.get(fact.casefold(), (fact.casefold(),))
    return any(alias in searchable for alias in aliases)


def evaluate_job(
    *,
    job: Mapping[str, object],
    confirmed_facts: list[str],
    configs: Mapping[str, DimensionConfig] | None = None,
) -> ScoreResult:
    """Score one job without inferring any skills a candidate has not confirmed.

    The lexical evidence is intentionally simple and shown back to the user: a
    confirmed fact earns relevance credit only when it appears in the job title
    or description. Finnish regulated requirements are separately hard-blocked.
    """
    title = str(job.get("title", ""))
    description = str(job.get("description", ""))
    searchable = f"{title}\n{description}".casefold()
    matched = [fact for fact in confirmed_facts if _fact_matches_job(fact, searchable)]
    requirements = parse_requirements(description)
    failures = hard_requirement_failures(
        required=requirements.hard_requirements,
        confirmed_facts=confirmed_facts,
    )

    known_terms = requirements.hard_requirements + requirements.preferred_requirements
    if known_terms:
        matched_terms = sum(term.casefold() in {fact.casefold() for fact in confirmed_facts} for term in known_terms)
        eligibility = round(matched_terms / len(known_terms) * 100)
    else:
        eligibility = min(100, 50 + len(matched) * 25)
    competitive_strength = min(100, 40 + len(matched) * 30)
    verification = str(job.get("verification_state", "UNVERIFIED"))
    confidence = _CONFIDENCE_BY_VERIFICATION.get(verification, 40)

    result = evaluate_score(
        eligibility=eligibility,
        competitive_strength=competitive_strength,
        confidence=confidence,
        configs=dict(configs or DEFAULT_CONFIGS),
        hard_failures=failures,
    )
    matched_text = ", ".join(matched) if matched else "None"
    explanations = [f"Matched confirmed facts: {matched_text}"] + result.explanations
    return ScoreResult(
        final_score=result.final_score,
        hard_blocked=result.hard_blocked,
        hard_failures=result.hard_failures,
        queue_eligible=result.queue_eligible,
        normalized_weights=result.normalized_weights,
        explanations=explanations,
    )
