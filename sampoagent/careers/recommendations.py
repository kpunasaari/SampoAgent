"""Small extensible occupation catalogue used when no ESCO index is imported."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OccupationRecommendation:
    title_en: str
    title_fi: str
    score: int
    supporting_facts: list[str]
    missing_requirements: list[str]
    auto_target: bool = False


_OCCUPATIONS = {
    "Warehouse Worker": ("Varastotyöntekijä", {"forklift operation", "warehouse", "picking", "logistics"}),
    "Terminal Worker": ("Terminaalityöntekijä", {"forklift operation", "logistics", "loading"}),
    "Logistics Worker": ("Logistiikkatyöntekijä", {"forklift operation", "logistics", "delivery"}),
    "Cleaner": ("Siivooja", {"cleaning", "hygiene", "facilities"}),
    "Customer Service Representative": ("Asiakaspalvelija", {"customer service", "sales", "english"}),
}


def recommend_occupations(skills: list[str], *, ignored: list[str]) -> list[OccupationRecommendation]:
    normalized = {skill.casefold() for skill in skills}
    ignored_set = {title.casefold() for title in ignored}
    matches: list[OccupationRecommendation] = []
    for title, (title_fi, related) in _OCCUPATIONS.items():
        if title.casefold() in ignored_set:
            continue
        supporting = sorted(normalized & related)
        if supporting:
            matches.append(OccupationRecommendation(title, title_fi, min(95, 55 + 20 * len(supporting)), supporting, []))
    return sorted(matches, key=lambda item: item.score, reverse=True)
