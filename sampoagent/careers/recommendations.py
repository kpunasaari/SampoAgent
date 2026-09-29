"""Explainable occupation matching backed by a local ESCO index when available."""

from dataclasses import dataclass
import re
import unicodedata
from typing import Sequence

from sampoagent.careers.taxonomy import TaxonomyOccupation
from sampoagent.country_packs.models import QualificationAdvisory
from sampoagent.country_packs.finland.qualifications import advisories_for_occupation


@dataclass(frozen=True)
class OccupationRecommendation:
    title_en: str
    title_fi: str
    score: int
    supporting_facts: list[str]
    missing_requirements: list[str]
    auto_target: bool = False
    qualification_advisories: tuple[QualificationAdvisory, ...] = ()


_OCCUPATIONS = {
    "Warehouse Worker": ("Varastotyöntekijä", {"forklift operation", "warehouse", "picking", "logistics"}),
    "Terminal Worker": ("Terminaalityöntekijä", {"forklift operation", "logistics", "loading"}),
    "Logistics Worker": ("Logistiikkatyöntekijä", {"forklift operation", "logistics", "delivery"}),
    "Cleaner": ("Siivooja", {"cleaning", "hygiene", "facilities"}),
    "Customer Service Representative": ("Asiakaspalvelija", {"customer service", "sales", "english"}),
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", plain))


def _matches(candidate_skill: str, taxonomy_labels: dict[str, str]) -> bool:
    candidate = _normalize(candidate_skill)
    if not candidate:
        return False
    candidate_words = set(candidate.split())
    for label in taxonomy_labels.values():
        normalized = _normalize(label)
        if candidate == normalized:
            return True
        label_words = set(normalized.split())
        shared = candidate_words & label_words
        if shared and len(shared) == min(len(candidate_words), len(label_words)) and len(shared) >= 2:
            return True
    return False


def _recommend_from_taxonomy(
    skills: list[str], ignored: list[str], taxonomy: Sequence[TaxonomyOccupation], country_code: str | None
) -> list[OccupationRecommendation]:
    ignored_set = {_normalize(title) for title in ignored}
    matches: list[OccupationRecommendation] = []
    for occupation in taxonomy:
        title_en = occupation.labels.get("en") or next(iter(occupation.labels.values()), "")
        title_fi = occupation.labels.get("fi") or title_en
        if not title_en or _normalize(title_en) in ignored_set or _normalize(title_fi) in ignored_set:
            continue
        supporting: dict[str, str] = {}
        matched_uris: set[str] = set()
        for candidate_skill in skills:
            for related in occupation.skills:
                if _matches(candidate_skill, related.labels):
                    supporting.setdefault(candidate_skill.casefold(), candidate_skill)
                    matched_uris.add(related.uri)
        if not supporting:
            continue
        essential = [related for related in occupation.skills if related.importance == "ESSENTIAL"]
        optional = [related for related in occupation.skills if related.importance != "ESSENTIAL"]
        total_weight = 3 * len(essential) + len(optional)
        matched_weight = 3 * sum(related.uri in matched_uris for related in essential) + sum(
            related.uri in matched_uris for related in optional
        )
        score = round(100 * matched_weight / total_weight) if total_weight else 0
        missing = sorted(
            {
                related.labels.get("en") or related.labels.get("fi") or next(iter(related.labels.values()), "")
                for related in essential
                if related.uri not in matched_uris
            }
        )
        matches.append(
            OccupationRecommendation(
                title_en=title_en,
                title_fi=title_fi,
                score=score,
                supporting_facts=sorted(supporting.values(), key=str.casefold),
                missing_requirements=[value for value in missing if value],
                auto_target=False,
                qualification_advisories=(advisories_for_occupation(title_en, title_fi, country_code=country_code)
                    if country_code else ()),
            )
        )
    return sorted(matches, key=lambda item: (-item.score, item.title_en.casefold()))[:20]


def recommend_occupations(
    skills: list[str], *, ignored: list[str], taxonomy: Sequence[TaxonomyOccupation] | None = None,
    country_code: str | None = None,
) -> list[OccupationRecommendation]:
    if taxonomy is not None:
        return _recommend_from_taxonomy(skills, ignored, taxonomy, country_code)
    normalized = {skill.casefold() for skill in skills}
    ignored_set = {title.casefold() for title in ignored}
    matches: list[OccupationRecommendation] = []
    for title, (title_fi, related) in _OCCUPATIONS.items():
        if title.casefold() in ignored_set:
            continue
        supporting = sorted(normalized & related)
        if supporting:
            matches.append(OccupationRecommendation(
                title, title_fi, min(95, 55 + 20 * len(supporting)), supporting, [],
                qualification_advisories=(advisories_for_occupation(title, title_fi, country_code=country_code)
                    if country_code else ()),
            ))
    return sorted(matches, key=lambda item: item.score, reverse=True)
