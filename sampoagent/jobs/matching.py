"""Exact, explainable candidate-to-requirement checks."""

from collections.abc import Mapping
from datetime import date
import re

from sampoagent.jobs.salary import parse_monthly_eur_salary


_PROFILE_LOCATION_CONNECTORS = {
    "and", "or", "near", "around", "except", "excluding", "any", "anywhere", "everywhere", "all",
    "no", "none", "preference", "flexible", "open", "relocate", "relocation",
    "move", "willing", "would", "like", "prefer", "preferred", "work", "in", "at", "to",
    "ja", "tai", "paitsi", "lukuun", "muuttaa", "muutto", "haluan", "toivon", "työskennellä",
    "ei", "kaikkialla", "utom", "eller", "och", "nära", "flytta", "flytt", "vill", "önskar",
}


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _profile_location_list(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return ""
    parts = re.split(r"[,;\n]+", value)
    clean: list[str] = []
    for part in parts:
        item = " ".join(part.split()).strip()
        words = _normalized_words(item).split()
        if (
            not item
            or len(item) > 60
            or not 1 <= len(words) <= 4
            or not re.fullmatch(r"[\w .'-]+", item, flags=re.UNICODE)
            or _PROFILE_LOCATION_CONNECTORS.intersection(words)
        ):
            return ""
        clean.append(item)
    return ", ".join(dict.fromkeys(clean))


def _exact_choice(value: object, choices: Mapping[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    return choices.get(_normalized_words(value))


def merge_confirmed_profile_preferences(
    saved: Mapping[str, object],
    answers: list[Mapping[str, object]],
    *,
    today: date | None = None,
) -> dict[str, object]:
    """Use only current, confirmed, global profile preferences as safe fallbacks.

    Explicit Settings filters win. Free-text answers are used only when their
    meaning is an exact supported choice or a simple location list; role
    interests and employer-scoped availability never create global search scope.
    """
    effective = dict(saved)
    now = today or date.today()
    eligible: dict[str, str] = {}
    for answer in answers:
        question_id = str(answer.get("question_id", ""))
        if (
            question_id not in {
                "preferences:locations", "preferences:workplace",
                "preferences:contract", "preferences:hours",
            }
            or question_id in eligible
            or answer.get("answer_state") != "CONFIRMED"
            or answer.get("scope_type") != "GLOBAL"
        ):
            continue
        valid_until = answer.get("valid_until")
        if valid_until:
            try:
                if date.fromisoformat(str(valid_until)) < now:
                    continue
            except ValueError:
                continue
        value = answer.get("value")
        if isinstance(value, str) and value.strip():
            eligible[question_id] = value.strip()

    if not str(effective.get("locations", "") or "").strip() and "preferences:locations" in eligible:
        locations = _profile_location_list(eligible["preferences:locations"])
        if locations:
            effective["locations"] = locations

    any_value = {"", "any"}
    if str(effective.get("work_type", "any") or "any").strip().casefold() in any_value:
        work_type = _exact_choice(eligible.get("preferences:workplace"), {
            "on site": "onsite", "onsite": "onsite", "in person": "onsite", "on premises": "onsite",
            "paikan päällä": "onsite", "lähityö": "onsite", "på plats": "onsite", "på arbetsplatsen": "onsite",
            "hybrid": "hybrid", "hybrid work": "hybrid", "hybridityö": "hybrid", "hybridarbete": "hybrid",
            "remote": "remote", "remote work": "remote", "work from home": "remote", "etätyö": "remote",
            "distansarbete": "remote",
        })
        if work_type:
            effective["work_type"] = work_type

    if str(effective.get("employment_type", "any") or "any").strip().casefold() in any_value:
        employment_type = _exact_choice(eligible.get("preferences:contract"), {
            "permanent": "permanent", "permanent employment": "permanent", "indefinite": "permanent",
            "toistaiseksi": "permanent", "toistaiseksi voimassa oleva": "permanent", "vakituinen": "permanent",
            "tillsvidare": "permanent", "tillsvidareanställning": "permanent",
            "temporary": "temporary", "fixed term": "temporary", "määräaikainen": "temporary",
            "tillfällig": "temporary", "visstidsanställning": "temporary",
            "seasonal": "seasonal", "summer job": "seasonal", "kesätyö": "seasonal",
            "kausityö": "seasonal", "säsongsarbete": "seasonal",
        })
        if employment_type:
            effective["employment_type"] = employment_type

    if str(effective.get("hours_type", "any") or "any").strip().casefold() in any_value:
        hours_type = _exact_choice(eligible.get("preferences:hours"), {
            "full time": "full_time", "kokoaikainen": "full_time", "kokopäiväinen": "full_time",
            "heltid": "full_time", "heltidsarbete": "full_time",
            "part time": "part_time", "osa aikainen": "part_time", "deltid": "part_time",
        })
        if hours_type:
            effective["hours_type"] = hours_type
    return effective


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
        "onsite": ("on-site", "onsite", "paikan päällä", "lähityö", "på plats", "på arbetsplatsen"),
        "hybrid": ("hybrid", "hybridi"),
        "remote": ("remote", "work from home", "work-from-home", "etätyö", "etä-", "distansarbete"),
    }
    if work_type in markers and not any(marker in searchable for marker in markers[work_type]):
        return False
    employment_markers = {
        "full_time": ("full-time", "full time", "kokoaikainen"),
        "part_time": ("part-time", "part time", "osa-aikainen"),
        "permanent": ("permanent", "indefinite", "toistaiseksi voimassa oleva", "vakituinen", "tillsvidare"),
        "temporary": ("temporary", "fixed-term", "fixed term", "määräaikainen", "sijaisuus", "tillfällig", "visstidsanställning"),
        "seasonal": ("seasonal", "summer job", "kesätyö", "kausityö", "säsongsarbete"),
    }
    employment_type = str(preferences.get("employment_type", "any"))
    if employment_type in employment_markers and not any(marker in searchable for marker in employment_markers[employment_type]):
        return False
    hours_markers = {
        "full_time": ("full-time", "full time", "kokoaikainen", "kokopäiväinen", "heltid"),
        "part_time": ("part-time", "part time", "osa-aikainen", "deltid"),
    }
    hours_type = str(preferences.get("hours_type", "any"))
    if hours_type in hours_markers and not any(marker in searchable for marker in hours_markers[hours_type]):
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
