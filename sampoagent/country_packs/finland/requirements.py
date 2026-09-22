"""Conservative parsing of common Finnish employment requirements."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequirementParse:
    hard_requirements: list[str]
    preferred_requirements: list[str]
    ambiguous_requirements: list[str]


_TERMS = ("B-ajokortti", "hygieniapassi", "työturvallisuuskortti", "anniskelupassi")


def parse_requirements(text: str) -> RequirementParse:
    lowered = text.casefold()
    found = [term for term in _TERMS if term.casefold() in lowered]
    if not found:
        return RequirementParse([], [], [])
    if any(marker in lowered for marker in ("vaaditaan", "edellytetään", "pakollinen")):
        return RequirementParse(found, [], [])
    if any(marker in lowered for marker in ("eduksi", "toivotaan", "arvostetaan")):
        return RequirementParse([], found, [])
    return RequirementParse([], [], found)
