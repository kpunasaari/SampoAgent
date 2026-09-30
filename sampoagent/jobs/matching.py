"""Exact, explainable candidate-to-requirement checks."""

from collections.abc import Mapping

from sampoagent.jobs.salary import parse_monthly_eur_salary


def hard_requirement_failures(*, required: list[str], confirmed_facts: list[str]) -> list[str]:
    confirmed = {value.casefold() for value in confirmed_facts}
    return [f"Missing mandatory requirement: {requirement}" for requirement in required if requirement.casefold() not in confirmed]


def source_category_allowed(
    *,
    source_type: object,
    preferences: Mapping[str, object],
    source_known: bool = True,
) -> bool:
    """Apply explicit source-category exclusions consistently across search and matching."""
    category = " ".join(
        str(source_type or "").casefold().replace("-", " ").replace("_", " ").split()
    )

    def opted_out(key: str) -> bool:
        value = preferences.get(key, "yes")
        if value is False or value == 0:
            return True
        return isinstance(value, str) and value.strip().casefold() in {"no", "false", "off", "0"}

    public_sector = any(
        marker in category
        for marker in ("public sector", "government", "municipal board", "public service")
    )
    recruitment_agency = any(
        marker in category
        for marker in ("recruitment agency", "staffing agency", "employment agency", "personnel agency")
    )
    if not source_known and (
        opted_out("include_public_sector") or opted_out("include_recruitment_agencies")
    ):
        return False
    if public_sector and opted_out("include_public_sector"):
        return False
    if recruitment_agency and opted_out("include_recruitment_agencies"):
        return False
    return True


def matches_preferences(*, job: Mapping[str, object], preferences: Mapping[str, object]) -> bool:
    """Apply only explicit, deterministic user filters to normalized job data."""
    source_type = job.get("source_type", "")
    source_known = job.get("source_id") is None or bool(str(source_type or "").strip())
    if not source_category_allowed(
        source_type=source_type,
        preferences=preferences,
        source_known=source_known,
    ):
        return False
    try:
        minimum_salary = max(0, int(preferences.get("salary_minimum", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        minimum_salary = 0
    if minimum_salary:
        salary = parse_monthly_eur_salary(str(job.get("description", "")))
        if salary and salary.maximum_eur is not None and salary.maximum_eur < minimum_salary:
            return False
    location = str(job.get("location", "")).casefold()
    locations = [item.strip().casefold() for item in str(preferences.get("locations", "")).split(",") if item.strip()]
    if locations and not any(item in location for item in locations):
        return False
    excluded_locations = [item.strip().casefold() for item in str(preferences.get("locations_exclude", "")).split(",") if item.strip()]
    if any(item in location for item in excluded_locations):
        return False
    title = str(job.get("title", "")).casefold()
    employer = str(job.get("company", "")).casefold()
    searchable = f"{title}\n{job.get('description', '')}".casefold()
    keywords = [item.strip().casefold() for item in str(preferences.get("keywords", "")).split(",") if item.strip()]
    if keywords and not any(item in searchable for item in keywords):
        return False
    for preference_key, field in (("title_include", title), ("employer_include", employer)):
        terms = [item.strip().casefold() for item in str(preferences.get(preference_key, "")).split(",") if item.strip()]
        if terms and not any(term in field for term in terms):
            return False
    for preference_key, field in (("title_exclude", title), ("employer_exclude", employer)):
        terms = [item.strip().casefold() for item in str(preferences.get(preference_key, "")).split(",") if item.strip()]
        if any(term in field for term in terms):
            return False
    work_type = str(preferences.get("work_type", "any"))
    markers = {
        "onsite": ("on-site", "onsite", "paikan päällä"),
        "hybrid": ("hybrid", "hybridi"),
        "remote": ("remote", "etätyö", "etä-"),
    }
    if work_type in markers and not any(marker in searchable for marker in markers[work_type]):
        return False
    employment_markers = {
        "full_time": ("full-time", "full time", "kokoaikainen"),
        "part_time": ("part-time", "part time", "osa-aikainen"),
        "temporary": ("temporary", "fixed-term", "määräaikainen", "sijaisuus"),
        "seasonal": ("seasonal", "summer job", "kesätyö", "kausityö"),
    }
    employment_type = str(preferences.get("employment_type", "any"))
    if employment_type in employment_markers and not any(marker in searchable for marker in employment_markers[employment_type]):
        return False
    schedule_markers = {
        "day": ("day shift", "päivävuoro"),
        "evening": ("evening shift", "iltavuoro"),
        "night": ("night shift", "yövuoro"),
        "weekend": ("weekend", "viikonloppu"),
    }
    schedule = str(preferences.get("schedule", "any"))
    if schedule in schedule_markers and not any(marker in searchable for marker in schedule_markers[schedule]):
        return False
    return True
