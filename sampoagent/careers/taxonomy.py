"""Typed records for locally imported occupation and skill taxonomies."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxonomyOccupationSkill:
    uri: str
    importance: str
    labels: dict[str, str]


@dataclass(frozen=True)
class TaxonomyOccupation:
    uri: str
    labels: dict[str, str]
    descriptions: dict[str, str]
    isco_code: str
    skills: tuple[TaxonomyOccupationSkill, ...]
