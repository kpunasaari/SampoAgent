"""Conservative Finland qualification advisories backed by official sources.

These rules flag a need to check an authority's decision or permit. They do not
decide whether a candidate is qualified, whether a permit applies to a vacancy,
or whether a candidate is legally ineligible.
"""

from dataclasses import dataclass
import re
import unicodedata

from sampoagent.country_packs.models import QualificationAdvisory


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", plain))


@dataclass(frozen=True)
class _Rule:
    code: str
    occupation_terms: tuple[str, ...]
    message: str
    source_title: str
    source_url: str


_RULES = (
    _Rule(
        code="fi-health-social-practice-right",
        occupation_terms=(
            "registered nurse", "nursing professional", "nurse", "physician", "medical doctor",
            "doctor", "dentist", "midwife", "pharmacist", "practical nurse", "psychologist",
            "psychotherapist", "physiotherapist", "occupational therapist", "social worker",
            "sairaanhoitaja", "lähihoitaja", "lääkäri", "hammaslääkäri", "kätilö", "farmaseutti",
            "proviisori", "psykologi", "psykoterapeutti", "fysioterapeutti", "toimintaterapeutti",
            "sosiaalityöntekijä", "sjukskötare", "läkare", "tandläkare", "barnmorska", "farmaceut",
            "psykolog", "fysioterapeut", "arbetsterapeut", "socialarbetare",
        ),
        message=(
            "Check with the Finnish Supervisory Agency whether this role requires a professional practice right "
            "and whether your qualification/registration is recognised. This is an authority-check prompt, "
            "not a finding that you are unqualified."
        ),
        source_title="Finnish Supervisory Agency — right to practise in social welfare or health care",
        source_url="https://www.suomi.fi/services/eservice/apply-for-the-right-to-practise-in-social-welfare-or-health-care-finnish-supervisory-agency/386fb71c-7984-492a-8ece-35ccb90861f0",
    ),
    _Rule(
        code="fi-ecec-eligibility",
        occupation_terms=(
            "early childhood education and care teacher", "early childhood social pedagogue",
            "early childhood special education teacher", "head of early education centre",
            "early education centre director", "early childhood education and care childcarer",
            "varhaiskasvatuksen opettaja", "varhaiskasvatuksen sosionomi", "varhaiskasvatuksen erityisopettaja",
            "varhaiskasvatuksen lastenhoitaja", "päiväkodin johtaja", "småbarnspedagogik lärare",
            "lärare inom småbarnspedagogik", "socionom inom småbarnspedagogik",
        ),
        message=(
            "For Finland's listed early-childhood education and care professions, qualification rules are set in law; "
            "foreign qualifications may need a Finnish National Agency for Education decision. Verify the exact role."
        ),
        source_title="Finnish National Agency for Education — recognition of ECEC qualifications",
        source_url="https://www.oph.fi/en/services/recognition-qualifications/recognition-early-childhood-education-and-care-qualifications",
    ),
    _Rule(
        code="fi-private-security-approval",
        occupation_terms=(
            "security guard", "temporary guard", "security steward", "security officer",
            "vartija", "järjestyksenvalvoja", "väliaikainen vartija", "ordningsvakt", "väktare",
        ),
        message=(
            "Private-security duties in Finland can require role-specific police approval/card and training. "
            "Check the exact duty and current conditions with the Police/employer; this notice does not assess eligibility."
        ),
        source_title="Police of Finland — permits for the private security sector",
        source_url="https://poliisi.fi/en/permit-forms-for-the-private-security-sector",
    ),
)


def advisories_for_occupation(title_en: str, title_fi: str, *, country_code: str) -> tuple[QualificationAdvisory, ...]:
    """Return sourced prompts for a title; never classify it as a legal hard gap."""
    if country_code.strip().upper() != "FI":
        return ()
    titles = {_normalize(title_en), _normalize(title_fi)} - {""}
    advisories = []
    for rule in _RULES:
        if any(_normalize(term) in title for term in rule.occupation_terms for title in titles):
            advisories.append(QualificationAdvisory(
                code=rule.code,
                message=rule.message,
                source_title=rule.source_title,
                source_url=rule.source_url,
                checked_on="2026-09-29",
            ))
    return tuple(advisories)
