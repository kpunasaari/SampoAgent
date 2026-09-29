"""Country-specific qualification guidance; advisories never determine eligibility."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QualificationAdvisory:
    code: str
    message: str
    source_title: str
    source_url: str
    checked_on: str
    is_legal_hard_gap: bool = False
