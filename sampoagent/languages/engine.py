"""Deterministic language choice; no translation call is needed."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageEngine:
    code: str
    cv_heading: str
    application_style: str


FINNISH = LanguageEngine("fi", "Ansioluettelo", "Tiivis, konkreettinen ja asiallinen suomi")
ENGLISH = LanguageEngine("en", "Curriculum Vitae", "Concise, factual professional English")


def language_engine_for_job(text: str, *, override: str | None = None) -> LanguageEngine:
    if override in {"fi", "en"}:
        return FINNISH if override == "fi" else ENGLISH
    lowered = text.casefold()
    return FINNISH if any(token in lowered for token in ("etsimme", "työntekij", "vaaditaan", "vuorotyö", "tehtävä")) else ENGLISH
