"""Deterministic, profile-led job search planning without AI or site scraping."""

from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import quote_plus, urlsplit

from sampoagent.careers.recommendations import recommend_occupations


@dataclass(frozen=True)
class SearchQuery:
    source_id: int
    source_name: str
    phrase: str
    location: str
    search_url: str
    capability: str


@dataclass(frozen=True)
class SearchPlan:
    terms: tuple[str, ...]
    locations: tuple[str, ...]
    queries: tuple[SearchQuery, ...]


def _enabled(row: Mapping[str, object]) -> bool:
    value = row.get("enabled", True)
    return value not in (False, 0, "0", "false", "False")


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def _locations(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        parts = []
    return _unique(parts)


def _search_url(source_url: str, phrase: str, location: str) -> str:
    host = urlsplit(source_url).hostname
    if not host:
        return ""
    terms = [f"site:{host}", phrase, location, "jobs" if phrase.isascii() else "työpaikat"]
    return "https://www.google.com/search?q=" + quote_plus(" ".join(term for term in terms if term))


def build_search_plan(
    *,
    facts: Sequence[Mapping[str, object]],
    candidate_records: Mapping[str, Sequence[Mapping[str, object]]],
    targets: Sequence[Mapping[str, object]],
    career_profiles: Sequence[Mapping[str, object]],
    preferences: Mapping[str, object],
    sources: Sequence[Mapping[str, object]],
    max_queries: int = 120,
) -> SearchPlan:
    """Build source-scoped web searches from confirmed candidate evidence.

    The links open a web search scoped to each source domain. This planner does
    not visit or scrape the job source; direct indexing requires a feed or an
    officially supported adapter.
    """
    terms: list[str] = []
    confirmed_skills: list[str] = []
    for fact in facts:
        if not _enabled(fact) or not _enabled({"enabled": not bool(fact.get("rejected", False))}):
            continue
        if fact.get("confirmed") not in (True, 1, "1", "true", "True"):
            continue
        value = str(fact.get("value", "")).strip()
        if not value:
            continue
        fact_type = str(fact.get("type", "")).casefold()
        if fact_type == "skill":
            confirmed_skills.append(value)
        elif fact_type in {"experience", "job_title", "position", "work_title"}:
            terms.append(value)

    for target in targets:
        if _enabled(target):
            terms.extend(
                str(target[key]).strip()
                for key in ("title_fi", "title_en")
                if target.get(key)
            )
    for profile in career_profiles:
        if _enabled(profile) and profile.get("name"):
            terms.append(str(profile["name"]))
    for record in candidate_records.get("experience", ()):
        title = str(record.get("title", "")).strip()
        if title:
            terms.append(title)

    for recommendation in recommend_occupations(confirmed_skills, ignored=[]):
        terms.extend((recommendation.title_fi, recommendation.title_en))

    terms.extend(_locations(preferences.get("search_terms_include", "")))
    excluded_terms = {term.casefold() for term in _locations(preferences.get("search_terms_exclude", ""))}
    unique_terms = tuple(term for term in _unique(terms) if term.casefold() not in excluded_terms)
    locations = _locations(preferences.get("locations", ""))
    location_options = locations or ("",)
    active_sources = [source for source in sources if _enabled(source)]
    queries: list[SearchQuery] = []
    bound = max(0, max_queries)
    for source in active_sources:
        source_url = str(source.get("url", ""))
        if not source_url.startswith("https://"):
            continue
        for phrase in unique_terms:
            for location in location_options:
                if len(queries) >= bound:
                    break
                queries.append(
                    SearchQuery(
                        source_id=int(source.get("id", 0)),
                        source_name=str(source.get("name", "Job source")),
                        phrase=phrase,
                        location=location,
                        search_url=_search_url(source_url, phrase, location),
                        capability=str(source.get("capability", "Browser search only")),
                    )
                )
            if len(queries) >= bound:
                break
        if len(queries) >= bound:
            break

    return SearchPlan(terms=unique_terms, locations=locations, queries=tuple(queries))
