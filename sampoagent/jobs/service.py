"""Deterministic opportunity intelligence without scraping restricted sites."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit
import re


@dataclass(frozen=True)
class NormalizedJob:
    title: str
    company: str
    location: str
    description: str
    language: str
    application_url: str
    fingerprint: str


def detect_language(text: str) -> str:
    finnish_markers = (" ja ", " on ", " työ", "etsimme", "vaaditaan", "suomi")
    return "fi" if any(marker in f" {text.casefold()}" for marker in finnish_markers) else "en"


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc.casefold(), parsed.path.rstrip("/"), "", ""))


def _key(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def normalize_job(*, title: str, company: str, location: str, description: str, application_url: str) -> NormalizedJob:
    fingerprint = sha256(f"{_key(title)}|{_key(company)}|{_key(location)}|{_key(description)[:400]}".encode()).hexdigest()
    return NormalizedJob(title.strip(), company.strip(), location.strip(), description.strip(), detect_language(f"{title} {description}"), canonical_url(application_url), fingerprint)


def is_duplicate(first: NormalizedJob, second: NormalizedJob) -> bool:
    return bool(first.application_url and first.application_url == second.application_url) or first.fingerprint == second.fingerprint


def verification_state(*, deadline: date | None, employer: str, application_url: str) -> str:
    if deadline and deadline < date.today():
        return "EXPIRED"
    if employer and application_url:
        return "PARTIALLY_VERIFIED"
    return "UNVERIFIED"
