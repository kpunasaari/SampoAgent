"""Application safety policy: uncertainty always stops final submission."""

from enum import StrEnum


class ApplicationMode(StrEnum):
    REVIEW_EVERYTHING = "review_everything"
    SMART_APPROVAL = "smart_approval"
    AUTOPILOT = "autopilot"


def classify_question(question: str) -> str:
    lowered = question.casefold()
    if any(term in lowered for term in ("criminal", "health", "medical", "security clearance", "immigration", "work authorization", "authorized to work", "right to work", "työlupa", "työskentelyoikeus", "legal")):
        return "HIGH"
    if any(term in lowered for term in ("salary", "motivation", "start date", "experience")):
        return "MEDIUM"
    return "LOW"


def can_submit(mode: ApplicationMode, *, dry_run: bool, risk: str, applied_today: int, daily_limit: int) -> bool:
    if dry_run or mode == ApplicationMode.REVIEW_EVERYTHING or risk == "HIGH":
        return False
    if daily_limit == 0 or applied_today >= daily_limit:
        return False
    return risk == "LOW"
