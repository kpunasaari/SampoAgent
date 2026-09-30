"""Select a sufficiently matched archived CV or generate a factual job-specific one."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader

from sampoagent.candidate.service import read_cv_file
from sampoagent.country_packs.finland.requirements import parse_requirements
from sampoagent.cv.service import ROLE_FAMILIES, check_pdf_text, generate_cv_pdf
from sampoagent.scoring.evidence import record_text_matches


@dataclass(frozen=True)
class CVChoice:
    path: Path
    strategy: str
    role_family: str
    language: str
    score: int
    text_check_score: int
    text_check_performed: bool
    reasons: tuple[str, ...]
    checksum: str


def role_family_for_job(job: dict[str, object]) -> str:
    text = f"{job.get('title', '')} {job.get('description', '')}".casefold()
    groups = (
        ("cleaning_facilities", ("clean", "siiv", "lauan", "facility")),
        ("hotel_hospitality", ("hotel", "hospitality", "hotelli", "vastaanotto")),
        ("restaurant_kitchen", ("restaurant", "kitchen", "ravintola", "keitti")),
        ("warehouse_logistics", ("warehouse", "logistics", "varasto", "logisti", "forklift", "trukki")),
        ("customer_service", ("customer service", "asiakaspalvel", "call center")),
        ("retail", ("retail", "store worker", "myymäl", "kauppias")),
        ("transport_delivery", ("driver", "delivery", "kuljett", "jakelu")),
        ("construction", ("construction", "rakennus", "maalari", "painter")),
        ("healthcare", ("healthcare", "nurse", "terveys", "sairaanhoit")),
        ("social_care", ("social care", "sosiaal", "care worker", "hoiva")),
        ("education_childcare", ("teacher", "education", "opett", "varhaiskasv")),
        ("office_administration", ("administr", "office", "toimisto", "hallinto")),
        ("sales", ("sales", "myynti", "account manager")),
        ("marketing_communications", ("marketing", "viestint", "markkinoint")),
        ("it_software", ("software", "developer", "programmer", "ohjelmist", "it-")),
        ("security", ("security guard", "security", "vartija", "turvallisuus")),
        ("manufacturing_production", ("production", "manufactur", "tuotanto", "tehdas")),
        ("agriculture_outdoor", ("agriculture", "farm", "puutarha", "maatalous")),
    )
    for family, terms in groups:
        if any(term in text for term in terms):
            return family
    return "universal"


def _text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        if path.suffix.casefold() == ".pdf":
            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if path.suffix.casefold() in {".txt", ".docx"}:
            return read_cv_file(path)
    except Exception:
        return ""
    return ""


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).casefold().strip()


def _contains_current_candidate_identity(text: str, candidate: dict[str, str]) -> bool:
    """Avoid reusing a reviewed PDF that would carry another profile's identity forward."""
    normalized = _normalized_text(text)
    name = str(candidate.get("name", "")).strip()
    email = str(candidate.get("email", "")).strip()
    if not name or _normalized_text(name) not in normalized:
        return False
    if email and _normalized_text(email) not in normalized:
        return False
    phone = str(candidate.get("phone", "")).strip()
    if phone and _normalized_text(phone) not in normalized:
        return False
    embedded_emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if embedded_emails and (not email or any(_normalized_text(item) != _normalized_text(email) for item in embedded_emails)):
        return False
    return True


def _confirmed_records(records: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
    return {
        record_type: [
            record for record in items
            if str(record.get("review_state", "CONFIRMED")).upper() == "CONFIRMED"
        ]
        for record_type, items in records.items()
    }


def assess_cv_fit(
    cv_text: str,
    *,
    job: dict[str, object],
    facts: list[dict[str, object]],
    records: dict[str, list[dict[str, str]]],
    role_family: str,
    cv_role_family: str,
    cv_language: str,
    language: str,
) -> tuple[int, tuple[str, ...]]:
    if (role_family != cv_role_family and cv_role_family != "universal") or language != cv_language:
        return 0, ("Role or language does not match this vacancy",)
    lowered = cv_text.casefold()
    description = f"{job.get('title', '')}\n{job.get('description', '')}".casefold()
    parsed = parse_requirements(str(job.get("description", "")))
    requirements = parsed.hard_requirements + parsed.preferred_requirements
    relevant_facts = [str(fact["value"]) for fact in facts if bool(fact.get("confirmed")) and str(fact.get("value", "")).casefold() in description]
    relevant_records: list[str] = []
    for record_type, items in records.items():
        if record_type not in {"experience", "education", "certificate", "licence", "language"}:
            continue
        for record in items:
            values = [
                value for value in (
                    str(record.get("title") or ""),
                    str(record.get("name") or ""),
                    str(record.get("details") or ""),
                )
                if value.strip()
            ]
            if record_text_matches(" ".join(values), description):
                relevant_records.extend(values)
    checks = list(dict.fromkeys([*requirements, *relevant_facts, *relevant_records]))
    if not checks:
        return 0, ("No supported job-specific evidence was found to assess reuse",)
    missing_hard = [requirement for requirement in parsed.hard_requirements if requirement.casefold() not in lowered]
    matched = sum(term.casefold() in lowered for term in checks)
    score = round(matched * 100 / len(checks))
    if cv_role_family == "universal":
        score = max(0, score - 10)
    reasons = [f"Matched {matched} of {len(checks)} job-relevant evidence items"]
    if missing_hard:
        reasons.append("Required evidence missing: " + ", ".join(missing_hard))
    return score, tuple(reasons)


def choose_application_cv(*, output_dir: Path, archived: list[dict[str, object]], job: dict[str, object], candidate: dict[str, str], facts: list[dict[str, object]], records: dict[str, list[dict[str, str]]], learning_adjustments: dict[str, int] | None = None, preferred_path: str | Path | None = None) -> CVChoice:
    """Re-use only a same-language, same-role archived CV meeting 85% evidence fit."""
    language = str(job.get("language", "en"))
    if language not in {"fi", "en"}:
        language = "en"
    family = role_family_for_job(job)
    usable_facts = [fact for fact in facts if bool(fact.get("confirmed")) and not bool(fact.get("rejected"))]
    usable_records = _confirmed_records(records)
    learning = learning_adjustments or {}
    preferred_resolved = Path(preferred_path).resolve() if preferred_path else None
    reusable: list[tuple[int, int, int, CVChoice]] = []
    corrupt_archive_found = False
    identity_mismatch_found = False
    preferred_choice: CVChoice | None = None
    preferred_rejection_reason = ""

    def is_preferred(path: Path) -> bool:
        return preferred_resolved is not None and path.resolve() == preferred_resolved

    def with_reason(choice: CVChoice, reason: str) -> CVChoice:
        return CVChoice(
            choice.path, choice.strategy, choice.role_family, choice.language, choice.score,
            choice.text_check_score, choice.text_check_performed, (*choice.reasons, reason), choice.checksum,
        )

    for index, item in enumerate(archived):
        path = Path(str(item.get("path", "")))
        selected = is_preferred(path)
        if not path.is_file() or not item.get("checksum"):
            corrupt_archive_found = True
            if selected:
                preferred_rejection_reason = "Candidate-selected archived CV was unavailable or had no reviewed checksum"
            continue
        try:
            file_bytes = path.read_bytes()
        except OSError:
            corrupt_archive_found = True
            if selected:
                preferred_rejection_reason = "Candidate-selected archived CV could not be read"
            continue
        checksum = sha256(file_bytes).hexdigest()
        if checksum != str(item["checksum"]):
            corrupt_archive_found = True
            continue
        text = _text(path)
        if not _contains_current_candidate_identity(text, candidate):
            identity_mismatch_found = True
            if selected:
                preferred_rejection_reason = "Candidate-selected archived CV did not match the current candidate name, email or confirmed phone"
            continue
        score, reasons = assess_cv_fit(
            text, job=job, facts=usable_facts, records=usable_records, role_family=family,
            cv_role_family=str(item.get("role_family", "")), cv_language=str(item.get("language", "")), language=language,
        )
        parsed = parse_requirements(str(job.get("description", "")))
        lowered_text = text.casefold()
        hard_requirements_match = all(term.casefold() in lowered_text for term in parsed.hard_requirements)
        if score >= 85 and hard_requirements_match:
            adjustment = max(-8, min(8, int(learning.get(checksum, 0))))
            choice_reasons = reasons
            if adjustment:
                choice_reasons = (*reasons, f"Confirmed application outcomes influenced this archived CV choice ({adjustment:+d})")
            choice = CVChoice(
                path, "reused", family, language, score,
                int(item.get("text_check_score", item.get("ats_score", 0))),
                bool(item.get("text_check_performed")), choice_reasons, checksum,
            )
            reusable.append((score + adjustment, score, -index, choice))
            if selected:
                preferred_choice = choice
        elif selected:
            reasons = []
            if score < 85:
                reasons.append(f"supported vacancy-evidence fit was {score}%, below the 85% reuse threshold")
            if not hard_requirements_match:
                reasons.append("it did not contain every parsed hard requirement")
            preferred_rejection_reason = "Candidate-selected archived CV was not reused because " + " and ".join(reasons)

    if preferred_choice:
        return with_reason(preferred_choice, "Candidate-selected archived CV passed the current identity, role, language, evidence and hard-requirement checks")
    if reusable:
        best_fit = max(item[1] for item in reusable)
        # Outcome learning may break close ties, but never trade away more than
        # five points of present-day vacancy fit or any hard requirement.
        eligible = [item for item in reusable if item[1] >= best_fit - 5]
        selected_choice = max(eligible, key=lambda item: (item[0], item[1], item[2]))[3]
        if preferred_rejection_reason:
            return with_reason(selected_choice, preferred_rejection_reason + "; automatic fit-based selection was used")
        return selected_choice

    snapshot = {
        "job_id": job.get("id"), "title": job.get("title"), "description": job.get("description"),
        "company": str(job.get("company", "")).strip(),
        "language": language, "family": family, "candidate": candidate,
        "facts": sorted((str(fact.get("type")), str(fact.get("value"))) for fact in usable_facts),
        "records": usable_records,
    }
    digest = sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    filename = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"job_{job.get('id', 'new')}_{digest[:16]}") + ".pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    parsed = parse_requirements(str(job.get("description", "")))
    priority_terms = [str(job.get("title", "")), *parsed.hard_requirements, *parsed.preferred_requirements]
    path = generate_cv_pdf(
        output_dir=output_dir, language=language,
        role_family=family if family in ROLE_FAMILIES else "universal",
        candidate=candidate, facts=usable_facts, filename_pattern=filename,
        company=str(job.get("company", "")), records=usable_records,
        target_title=str(job.get("title", "")), priority_terms=priority_terms,
    )
    required = [candidate["name"]]
    if candidate.get("email"):
        required.append(candidate["email"])
    if candidate.get("phone"):
        required.append(candidate["phone"])
    required.extend(str(fact["value"]) for fact in usable_facts if fact.get("type") in {"skill", "language", "certificate", "licence"})
    required.extend(str(record.get("title") or record.get("name") or "") for group in usable_records.values() for record in group if record.get("title") or record.get("name"))
    report = check_pdf_text(path, required=required)
    if not report.passed:
        raise ValueError("Generated CV failed its PDF text-presence check")
    checksum = sha256(path.read_bytes()).hexdigest()
    match_score, match_reasons = assess_cv_fit(
        report.text, job=job, facts=usable_facts, records=usable_records, role_family=family,
        cv_role_family=family, cv_language=language, language=language,
    )
    reasons = ["No archived CV met the 85% role, language and evidence threshold", *match_reasons]
    if preferred_rejection_reason:
        reasons.append(preferred_rejection_reason + "; a job-specific CV was generated")
    if identity_mismatch_found:
        reasons.append("An archived CV was excluded because its name or email does not match the current candidate profile")
    if corrupt_archive_found:
        reasons.append("An archived file was excluded because its reviewed checksum was missing or no longer matched")
    return CVChoice(path, "generated", family, language, match_score, report.score, True, tuple(reasons), checksum)
