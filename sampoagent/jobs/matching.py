"""Exact, explainable candidate-to-requirement checks."""

from collections.abc import Mapping


def hard_requirement_failures(*, required: list[str], confirmed_facts: list[str]) -> list[str]:
    confirmed = {value.casefold() for value in confirmed_facts}
    return [f"Missing mandatory requirement: {requirement}" for requirement in required if requirement.casefold() not in confirmed]


def matches_preferences(*, job: Mapping[str, object], preferences: Mapping[str, object]) -> bool:
    """Apply only explicit, deterministic user filters to normalized job data."""
    location = str(job.get("location", "")).casefold()
    locations = [item.strip().casefold() for item in str(preferences.get("locations", "")).split(",") if item.strip()]
    if locations and not any(item in location for item in locations):
        return False
    searchable = f"{job.get('title', '')}\n{job.get('description', '')}".casefold()
    keywords = [item.strip().casefold() for item in str(preferences.get("keywords", "")).split(",") if item.strip()]
    if keywords and not any(item in searchable for item in keywords):
        return False
    work_type = str(preferences.get("work_type", "any"))
    markers = {
        "onsite": ("on-site", "onsite", "paikan päällä"),
        "hybrid": ("hybrid", "hybridi"),
        "remote": ("remote", "etätyö", "etä-"),
    }
    if work_type in markers and not any(marker in searchable for marker in markers[work_type]):
        return False
    return True
