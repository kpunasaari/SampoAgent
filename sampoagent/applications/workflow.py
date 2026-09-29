"""Application safety policy: uncertainty always stops final submission."""

from enum import StrEnum
from typing import Any


class ApplicationMode(StrEnum):
    REVIEW_EVERYTHING = "review_everything"
    SMART_APPROVAL = "smart_approval"
    AUTOPILOT = "autopilot"


def classify_question(question: str) -> str:
    lowered = question.casefold()
    high_risk_markers = (
        "criminal", "conviction", "felony", "health", "medical", "disability", "diagnosis",
        "health declaration", "hälsa", "hälsodeklar", "sairaus", "terveys", "terveystiedot", "vammaisuus",
        "rikosrekisteri", "rikostausta", "brottsregister", "security clearance", "security screening",
        "clearance", "säkerhetsprövning", "turvallisuusselvitys", "background check", "taustaselvitys",
        "immigration", "visa sponsorship", "sponsorship",
        "work authorization", "authorized to work", "right to work", "eligible to work",
        "work permit", "work eligibility", "työlupa", "työskentelyoikeus", "oikeus työskennellä",
        "rätt att arbeta", "arbetstillstånd", "passport", "nationality",
        "citizenship", "medborgarskap", "national identity", "identity number", "social security",
        "personal identity code", "henkilötunnus", "bank account", "bank details", "pankkitili", "tilinumero",
        "equal opportunity", "demographic", "gender", "race", "ethnicity",
        "sexual orientation", "religion", "political affiliation", "union membership",
        "assessment", "soveltuvuusarviointi", "lämplighetstest", "bedömning",
        "adjustment to the recruitment", "reasonable adjustment", "recruitment process adjustment",
        "mukautus", "rekrytointiprosessi", "anpassning av rekryteringsprocessen",
        "sukupuoli", "kön", "etninen alkuperä", "etnicitet", "jämlikhet",
        "privacy notice", "privacy policy", "privacy terms", "data processing", "consent to process",
        "data retention", "data storage", "tietosuojaseloste", "tietojen säilytys",
        "henkilötietojen käsittely", "integritetspolicy", "dataskydd", "datalagring",
        "terms and conditions", "accept the terms", "agree to our terms", "julkinen asiakirja",
        "julkisuuslaki", "i certify", "i declare", "truthful", "accuracy declaration", "legally binding",
    )
    if any(term in lowered for term in high_risk_markers):
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
        repository.hold_for_captcha(application_id, detected_url=result.final_url or "")
        repository.finish_submission_attempt(application_id, state="CAPTCHA_HOLD", message="CAPTCHA detected; no automatic solving or retry")
        return "CAPTCHA_HOLD"
    if result.outcome_unknown:
        repository.update_application_status(application_id, "SUBMITTED_UNVERIFIED", "The browser outcome is uncertain; automatic retry is disabled.", queue_state="DO_NOT_RETRY")
        repository.finish_submission_attempt(application_id, state="UNKNOWN", message="Browser outcome could not be verified; automatic retry disabled")
        return "SUBMITTED_UNVERIFIED"
    if result.submitted and result.final_url and result.message:
        repository.add_submission_evidence(application_id=application_id, final_url=result.final_url, confirmation_message=result.message, confirmation_id=result.confirmation_id, agent_provider="browser")
        repository.update_application_status(application_id, "APPLIED", "Employer confirmation recorded.", queue_state="COMPLETED")
        repository.finish_submission_attempt(application_id, state="SUBMITTED", message="Employer confirmation recorded")
        return "APPLIED"
    repository.update_application_status(
        application_id,
        "NEEDS_USER",
        result.message[:300] or "The form needs manual review.",
        queue_state="WAITING_USER",
    )
    repository.finish_submission_attempt(application_id, state="FAILED", message="Submission was not confirmed; user review required")
    return "NEEDS_USER"
