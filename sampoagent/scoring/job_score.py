"""Deterministic, job-specific scoring built from confirmed candidate facts."""

from collections.abc import Mapping, Sequence
import json
import re

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


_RECORD_TYPES = {"experience", "education", "certificate", "licence", "language", "availability"}
_CREDENTIAL_RECORD_TYPES = {"certificate", "licence"}
_GENERIC_RECORD_WORDS = {
    "a", "an", "and", "at", "as", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with",
    "experience", "experienced", "job", "position", "work", "worker", "responsible", "duties",
}


def _record_text_matches(record_text: str, job_text: str) -> bool:
    record_words = {
        word for word in re.findall(r"[^\W_]+", record_text.casefold(), flags=re.UNICODE)
        if len(word) > 2 and word not in _GENERIC_RECORD_WORDS
    }
    if not record_words:
        return False
    job_words = set(re.findall(r"[^\W_]+", job_text.casefold(), flags=re.UNICODE))
    overlap = len(record_words & job_words)
    minimum_overlap = 1 if len(record_words) <= 2 else (len(record_words) + 1) // 2
    return overlap >= minimum_overlap


def _confirmed_record_evidence(
    records: Mapping[str, Sequence[Mapping[str, object]]] | None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return only explicitly confirmed record text, labels and exact credentials."""
    matched_candidates: list[tuple[str, str, str]] = []
    credentials: list[str] = []
    for record_type, items in (records or {}).items():
        normalized_type = str(record_type).casefold()
        if normalized_type not in _RECORD_TYPES:
            continue
        for record in items:
            if str(record.get("review_state", "CONFIRMED")).upper() != "CONFIRMED":
                continue
            label = str(record.get("title") or record.get("name") or "").strip()
            details = str(record.get("details") or "").strip()
            if not label and not details:
                continue
            text = " ".join(part for part in (label, details) if part)
            matched_candidates.append((normalized_type, label or details[:100], text))
            if normalized_type in _CREDENTIAL_RECORD_TYPES:
                credentials.extend(
                    value for value in (str(record.get("title") or "").strip(), str(record.get("name") or "").strip())
                    if value
                )
    return matched_candidates, credentials


def evaluate_job(
    *,
    job: Mapping[str, object],
    confirmed_facts: list[str],
    confirmed_records: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    configs: Mapping[str, DimensionConfig] | None = None,
    learning_adjustment: int = 0,
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
    record_candidates, confirmed_credentials = _confirmed_record_evidence(confirmed_records)
    matched_records: list[tuple[str, str]] = []
    known_fact_keys = {" ".join(re.findall(r"[^\W_]+", fact.casefold())) for fact in matched}
    for record_type, label, text in record_candidates:
        if not _record_text_matches(text, searchable):
            continue
        label_key = " ".join(re.findall(r"[^\W_]+", label.casefold()))
        if label_key in known_fact_keys:
            continue
        evidence = (record_type, label)
        if evidence not in matched_records:
            matched_records.append(evidence)
    requirements = parse_requirements(description)
    failures = hard_requirement_failures(
        required=requirements.hard_requirements,
        confirmed_facts=[*confirmed_facts, *confirmed_credentials],
    )

    known_terms = requirements.hard_requirements + requirements.preferred_requirements
    if known_terms:
        exact_evidence = {fact.casefold() for fact in [*confirmed_facts, *confirmed_credentials]}
        matched_terms = sum(term.casefold() in exact_evidence for term in known_terms)
        eligibility = round(matched_terms / len(known_terms) * 100)
    else:
        eligibility = min(100, 50 + (len(matched) + len(matched_records)) * 25)
    evidence_count = len(matched) + len(matched_records)
    competitive_strength = max(0, min(100, 40 + evidence_count * 30 + max(-8, min(8, learning_adjustment))))
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
    if matched_records:
        explanations.append("Matched confirmed candidate records: " + ", ".join(
            f"{record_type} — {label}" for record_type, label in matched_records
        ))
    if learning_adjustment:
        explanations.append(f"Historical outcome adjustment for this role family: {learning_adjustment:+d} points")
    return ScoreResult(
        final_score=result.final_score,
        hard_blocked=result.hard_blocked,
        hard_failures=result.hard_failures,
        queue_eligible=result.queue_eligible,
        normalized_weights=result.normalized_weights,
        explanations=explanations,
    )
