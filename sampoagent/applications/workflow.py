"""Application safety policy: uncertainty always stops final submission."""

import re
from enum import StrEnum
from typing import Any


class ApplicationMode(StrEnum):
    REVIEW_EVERYTHING = "review_everything"
    SMART_APPROVAL = "smart_approval"
    AUTOPILOT = "autopilot"


def classify_question(question: str) -> str:
    # Treat labels as phrases rather than raw substrings: ``health`` should
    # match a disclosure question, not the ordinary job field ``healthcare``.
    lowered = re.sub(r"[-_/]+", " ", question.casefold())
    lowered = " ".join(lowered.split())
    high_risk_markers = (
        "criminal", "criminal record", "criminal history", "conviction*", "convicted", "felony",
        "arrest", "arrested", "charged with", "offence", "offense", "disease*", "illness*",
        "subject to disciplinary", "subjected to disciplinary", "faced disciplinary action",
        "received disciplinary action", "disciplinary action against", "disciplinary proceedings against",
        "disciplinary action taken against you", "had disciplinary action taken against",
        "dismissed for", "dismissed from a previous", "dismissed from a prior", "have you ever been dismissed",
        "fired for cause", "fired for misconduct", "fired from a previous", "have you ever been fired",
        "terminated for cause", "terminated for misconduct", "terminated from a previous", "terminated from a prior",
        "have you ever been terminated",
        "police caution", "police warning", "cautioned by police", "poliisilta huomautus*", "poliisin huomautus*",
        "poliisilta huomautuksen*", "poliisin huomautuksen*",
        "varning av polisen", "polisvarning*",
        "drug test", "drug screen", "drug screening", "substance test", "substance screen", "substance screening",
        "screening for drugs", "screening for alcohol", "alcohol test", "alcohol screen", "alcohol screening",
        "kurinpitomenettely*", "kurinpitotoimi*", "irtisanottu vakavan rikkomuksen vuoksi", "sinut irtisanottu", "sinut erotettu",
        "huumausainetesti*", "huumausaineseulonta*", "päihdetesti*", "päihdeseulonta*", "alkoholitesti*", "alkoholikoe*",
        "föremål för disciplinära åtgärder", "föremål för disciplinär åtgärd",
        "föremål för disciplinär åtgärder", "disciplinförfarande*", "avskedad på grund av allvarlig försummelse",
        "blivit avskedad", "blivit uppsagd",
        "drogtest*", "alkoholtest*", "alkoholprov*",
        "diagnosis", "disability", "disabilities", "reasonable accommodation", "medical history",
        "medical record", "medical information", "medical condition*", "medical screening",
        "medical examination", "medical test", "medical restriction", "medication",
        "health declaration", "health status", "health condition", "health history",
        "health information", "health issue*", "health problem*", "health restriction",
        "health limitation", "health questionnaire",
        "hälsa", "hälsodeklar*", "sairaus", "sairauks*", "terveys", "terveydentila", "terveystiedot",
        "vammaisuus", "vamma*", "rikostuomio*", "rikosrekisteri", "rikostausta", "pidätetty",
        "syytetty", "tuomittu", "brottsregister", "brott", "döm*", "åtalad", "arresterad",
        "sickness", "sjukdom*", "funktionsnedsättning", "hälsotillstånd",
        "security clearance", "security screening",
        "clearance", "säkerhetsprövning", "turvallisuusselvitys", "background check", "taustaselvitys",
        "background screening", "background checking", "immigration", "visa", "sponsorship",
        "work authorization", "authorized to work", "authorised to work", "right to work",
        "eligible to work", "legally work", "legally permitted to work", "legally allowed to work", "permission to work",
        "work permit", "work eligibility", "työlupa", "työskentelyoikeus", "oikeus työskennellä",
        "työviisumi", "työskentelylupa", "oleskelulupa", "rätt att arbeta", "arbetstillstånd",
        "arbeta lagligt", "passport", "nationality", "citizenship", "medborgarskap",
        "national identity", "identity number", "social security", "social security number", "ssn",
        "personal identity code", "date of birth", "birth date", "birthplace", "place of birth",
        "age", "how old are you", "your age", "födelsedatum", "födelseort", "ålder",
        "hur gammal är du", "kuinka vanha olet", "syntymäaika", "syntymävuosi", "syntymäpaikka",
        "henkilötunnus", "bank account", "bank details", "pankkitili", "tilinumero",
        "equal opportunity", "demographic", "gender", "sex", "gender identity", "race", "ethnicity",
        "ethnic origin", "marital status", "civil status", "pregnant", "pregnancy", "family status",
        "veteran status", "sexual orientation", "religion", "political affiliation", "union membership",
        "sukupuoli", "ikä", "siviilisääty", "perhesuhde", "raskaana", "etninen alkuperä",
        "uskonto", "poliittinen kanta", "ammattiyhdistys", "kön", "könsidentitet", "civilstånd",
        "familjesituation", "gravid", "etnicitet", "religion", "politisk tillhörighet", "fackförening",
        "assessment", "soveltuvuusarviointi*", "lämplighetstest*", "bedömning*",
        "adjustment to the recruitment", "reasonable adjustment", "reasonable accommodations",
        "recruitment process adjustment", "accommodations during", "interview accommodation",
        "mukautus*", "rekrytointiprosessi*", "anpassning*",
        "jämlikhet",
        "privacy notice", "privacy policy", "privacy terms", "data processing", "consent to process",
        "data retention", "data storage", "tietosuojaseloste*", "tietojen säilytys*",
        "henkilötietojen käsittely*", "integritetspolicy*", "dataskydd*", "datalagring*",
        "terms and conditions", "accept the terms", "agree to our terms", "julkinen asiakirja",
        "julkisuuslaki", "i certify", "i declare", "declare that", "attest that", "truthful",
        "true and complete", "true and correct", "accurate and complete", "correct and complete",
        "information provided is", "i confirm that the information", "accuracy declaration", "legally binding",
        "vahvistan antamani tiedot", "oikeiksi ja täydellisiksi", "tiedot ovat oikeita",
        "intygar", "uppgifterna är fullständiga och korrekta",
    )

    def has_marker(marker: str) -> bool:
        prefix = marker.endswith("*")
        phrase = marker[:-1] if prefix else marker
        parts = r"\s+".join(re.escape(part) for part in phrase.split())
        suffix = r"\w*" if prefix else r"(?!\w)"
        return re.search(rf"(?<!\w){parts}{suffix}", lowered) is not None

    if any(has_marker(term) for term in high_risk_markers):
        return "HIGH"
    if any(term in lowered for term in ("salary", "motivation", "start date", "experience", "notice period", "pay expectation", "hourly pay", "expected pay", "wage expectation")):
        return "MEDIUM"
    return "LOW"


def can_submit(
    mode: ApplicationMode,
    *,
    dry_run: bool,
    risk: str,
    applied_today: int,
    daily_limit: int,
    autopilot_authorized: bool = False,
    within_scope: bool = False,
    required_answers_resolved: bool = False,
    review_approved: bool = False,
    job_active: bool = False,
    duplicate: bool = True,
    captcha_detected: bool = False,
    paused: bool = True,
) -> bool:
    # The unattended grant does not cover legal, medical, criminal-history,
    # immigration, or other high-impact declarations.
    if risk == "HIGH" or risk not in {"LOW", "MEDIUM"}:
        return False
    if risk == "MEDIUM" and mode == ApplicationMode.REVIEW_EVERYTHING:
        return False
    if mode == ApplicationMode.SMART_APPROVAL and not review_approved:
        return False
    if mode == ApplicationMode.REVIEW_EVERYTHING:
        return False
    if dry_run or mode == ApplicationMode.REVIEW_EVERYTHING:
        return False
    if daily_limit == 0 or applied_today >= daily_limit:
        return False
    if paused or captcha_detected or duplicate or not job_active or not within_scope or not required_answers_resolved:
        return False
    if mode == ApplicationMode.AUTOPILOT:
        return autopilot_authorized
    return mode == ApplicationMode.SMART_APPROVAL and review_approved


def record_submission_result(repository: Any, application_id: int, result: Any) -> str:
    """Persist a browser outcome; ambiguous results are terminal until reconciled."""
    application = repository.application(application_id)
    if not application:
        raise ValueError("Application does not exist")
    if result.captcha_detected:
        repository.hold_for_captcha(application_id)
        repository.finish_submission_attempt(application_id, state="CAPTCHA_HOLD", message="CAPTCHA detected; no automatic solving or retry")
        return "CAPTCHA_HOLD"
    if result.outcome_unknown:
        repository.update_application_status(application_id, "SUBMITTED_UNVERIFIED", "The browser outcome is uncertain; automatic retry is disabled.", queue_state="DO_NOT_RETRY")
        repository.finish_submission_attempt(application_id, state="UNKNOWN", message="Browser outcome could not be verified; automatic retry disabled")
        return "SUBMITTED_UNVERIFIED"
    if result.submitted and result.final_url and result.message:
        repository.add_submission_evidence(
            application_id=application_id,
            final_url=result.final_url,
            confirmation_message="Employer confirmation detected on the page.",
            confirmation_id=_safe_browser_confirmation_id(result.confirmation_id),
            agent_provider="browser",
        )
        repository.update_application_status(application_id, "APPLIED", "Employer confirmation recorded.", queue_state="COMPLETED")
        repository.finish_submission_attempt(application_id, state="SUBMITTED", message="Employer confirmation recorded")
        return "APPLIED"
    repository.update_application_status(
        application_id,
        "NEEDS_USER",
        "The form needs manual review.",
        queue_state="WAITING_USER",
    )
    repository.finish_submission_attempt(application_id, state="FAILED", message="Submission was not confirmed; user review required")
    return "NEEDS_USER"


def _safe_browser_confirmation_id(value: object) -> str | None:
    """Keep only short, reference-shaped IDs from an untrusted browser adapter."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not re.fullmatch(r"[A-Za-z]{2,12}-[A-Za-z0-9]{1,12}", candidate):
        return None
    if re.search(r"\d{7,}", candidate) or any(
        marker in candidate.casefold()
        for marker in ("password", "passwd", "token", "secret", "email", "phone")
    ):
        return None
    return candidate
