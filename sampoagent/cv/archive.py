"""Select a sufficiently matched archived CV or generate a factual job-specific one."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader

from sampoagent.candidate.service import read_cv_file
from sampoagent.country_packs.finland.requirements import parse_requirements
from sampoagent.cv.service import ROLE_FAMILIES, generate_cv_pdf, validate_ats_pdf


@dataclass(frozen=True)
class CVChoice:
    path: Path
    strategy: str
    role_family: str
    language: str
    score: int
    ats_score: int
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
    relevant_records = [
        value
        for record_type, items in records.items()
        if record_type in {"experience", "education", "certificate", "licence", "language"}
        for record in items
        for value in (str(record.get("title") or ""), str(record.get("name") or ""), str(record.get("details") or ""))
        if value.strip() and value.casefold() in description
    ]
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


def choose_application_cv(*, output_dir: Path, archived: list[dict[str, object]], job: dict[str, object], candidate: dict[str, str], facts: list[dict[str, object]], records: dict[str, list[dict[str, str]]], learning_adjustments: dict[str, int] | None = None) -> CVChoice:
    """Re-use only a same-language, same-role archived CV meeting 85% evidence fit."""
    language = str(job.get("language", "en"))
    if language not in {"fi", "en"}:
        language = "en"
    family = role_family_for_job(job)
    usable_facts = [fact for fact in facts if bool(fact.get("confirmed")) and not bool(fact.get("rejected"))]
    usable_records = _confirmed_records(records)
    learning = learning_adjustments or {}
    reusable: list[tuple[int, int, int, CVChoice]] = []
    corrupt_archive_found = False
    for index, item in enumerate(archived):
        path = Path(str(item.get("path", "")))
        if not path.is_file() or not item.get("checksum"):
            corrupt_archive_found = True
            continue
        try:
            file_bytes = path.read_bytes()
        except OSError:
            corrupt_archive_found = True
            continue
        checksum = sha256(file_bytes).hexdigest()
        if checksum != str(item["checksum"]):
            corrupt_archive_found = True
            continue
        text = _text(path)
        score, reasons = assess_cv_fit(
            text, job=job, facts=usable_facts, records=usable_records, role_family=family,
            cv_role_family=str(item.get("role_family", "")), cv_language=str(item.get("language", "")), language=language,
        )
        parsed = parse_requirements(str(job.get("description", "")))
        lowered_text = text.casefold()
        if score >= 85 and all(term.casefold() in lowered_text for term in parsed.hard_requirements):
            adjustment = max(-8, min(8, int(learning.get(checksum, 0))))
            choice_reasons = reasons
            if adjustment:
                choice_reasons = (*reasons, f"Confirmed application outcomes influenced this archived CV choice ({adjustment:+d})")
            reusable.append((score + adjustment, score, -index, CVChoice(
                path, "reused", family, language, score, int(item.get("ats_score", 0)),
                bool(item.get("text_check_performed")), choice_reasons, checksum,
            )))

    if reusable:
        best_fit = max(item[1] for item in reusable)
        # Outcome learning may break close ties, but never trade away more than
        # five points of present-day vacancy fit or any hard requirement.
        eligible = [item for item in reusable if item[1] >= best_fit - 5]
        return max(eligible, key=lambda item: (item[0], item[1], item[2]))[3]

    snapshot = {
        "job_id": job.get("id"), "title": job.get("title"), "description": job.get("description"),
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
    required.extend(str(fact["value"]) for fact in usable_facts if fact.get("type") in {"skill", "language", "certificate", "licence"})
    required.extend(str(record.get("title") or record.get("name") or "") for group in usable_records.values() for record in group if record.get("title") or record.get("name"))
    report = validate_ats_pdf(path, required=required)
    if not report.passed:
        raise ValueError("Generated CV failed its ATS text re-parse")
    checksum = sha256(path.read_bytes()).hexdigest()
    match_score, match_reasons = assess_cv_fit(
        report.text, job=job, facts=usable_facts, records=usable_records, role_family=family,
        cv_role_family=family, cv_language=language, language=language,
    )
    reasons = ["No archived CV met the 85% role, language and evidence threshold", *match_reasons]
    if corrupt_archive_found:
        reasons.append("An archived file was excluded because its reviewed checksum was missing or no longer matched")
    return CVChoice(path, "generated", family, language, match_score, report.score, True, tuple(reasons), checksum)
