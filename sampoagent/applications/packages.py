"""Verified, ATS-checked CV packages for queued applications."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import shutil

from sampoagent.applications.urls import is_safe_public_https_url
from sampoagent.cv.archive import CVChoice, choose_application_cv


@dataclass(frozen=True)
class PreparedCV:
    choice: CVChoice
    note: str


def prepare_job_cv(repository: object, job: dict[str, object], storage_dir: Path, *, requested_path: str = "") -> PreparedCV:
    archived = repository.cv_archives()
    profile = repository.profile()
    if not profile:
        raise ValueError("Create a candidate profile before preparing an application")
    if requested_path:
        item = next((entry for entry in archived if str(entry["path"]) == requested_path), None)
        path = Path(requested_path)
        if not item or not path.is_file():
            raise ValueError("Select an archived CV or leave the field on automatic selection")
        checksum = sha256(path.read_bytes()).hexdigest()
        if not item.get("checksum") or checksum != str(item["checksum"]):
            raise ValueError("The selected archived CV checksum changed; re-upload and review the file")
        choice = CVChoice(path, "reused", str(item["role_family"]), str(item["language"]), int(item["fit_score"]), int(item.get("ats_score", 0)), ("Candidate-selected archived CV",), checksum)
        return PreparedCV(choice, "Candidate-selected archived CV.")
    record_types = ("experience", "education", "certificate", "licence")
    records = {kind: repository.candidate_records(kind) for kind in record_types}
    choice = choose_application_cv(
        output_dir=storage_dir / "archive", archived=archived, job=job, candidate=profile,
        facts=repository.rows("facts"), records=records,
        learning_adjustments=repository.cv_learning_adjustments(),
    )
    note = f"Tailored CV {choice.strategy}; job evidence fit {choice.score}%; ATS text check {choice.ats_score}%; {', '.join(choice.reasons)}"
    return PreparedCV(choice, note)


def copy_cv_to_application(source: str | Path, storage_dir: Path, application_id: int) -> Path:
    original = Path(source)
    if not original.is_file() or application_id < 1:
        raise ValueError("A readable archived CV and valid application ID are required")
    folder = storage_dir / "applications" / str(application_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / original.name
    version = 2
    while target.exists() and sha256(target.read_bytes()).hexdigest() != sha256(original.read_bytes()).hexdigest():
        target = folder / f"{original.stem}-{version}{original.suffix}"
        version += 1
    if target.resolve() != original.resolve() and not target.exists():
        shutil.copy2(original, target)
    if sha256(target.read_bytes()).hexdigest() != sha256(original.read_bytes()).hexdigest():
        raise OSError("Application CV copy did not match its archived checksum")
    return target


def enqueue_eligible_applications(repository: object, storage_dir: Path) -> int:
    """Prepare verified, in-scope jobs without bypassing hard score blockers."""
    from sampoagent.applications.runner import _active_verified_job
    from sampoagent.jobs.matching import matches_preferences
    from sampoagent.scoring.job_score import evaluate_job, load_configs
    from sampoagent.cv.archive import role_family_for_job

    queued = 0
    facts = repository.confirmed_fact_values()
    confirmed_records = {
        record_type: repository.candidate_records(record_type)
        for record_type in ("experience", "education", "certificate", "licence", "language", "availability")
    }
    configs = load_configs(repository.setting("scoring_config"))
    for job in repository.rows("jobs"):
        if repository.has_application_for_job(int(job["id"])) or not _active_verified_job(job):
            continue
        if not matches_preferences(job=job, preferences=repository.preferences()):
            continue
        score = evaluate_job(
            job=job, confirmed_facts=facts, confirmed_records=confirmed_records, configs=configs,
            learning_adjustment=repository.learning_adjustment(role_family_for_job(job)),
        )
        if not score.queue_eligible:
            continue
        try:
            prepared = prepare_job_cv(repository, job, storage_dir)
            choice = prepared.choice
            repository.add_document(kind="generated_cv", path=str(choice.path), checksum=choice.checksum)
            repository.archive_cv(
                path=str(choice.path), checksum=choice.checksum, language=choice.language,
                role_family=choice.role_family, source_job_id=int(job["id"]),
                fit_score=choice.score, ats_score=choice.ats_score, strategy=choice.strategy,
            )
            application_id = repository.queue_application(int(job["id"]), language=str(job["language"]), cv_path=str(choice.path), notes=prepared.note)
            copied = copy_cv_to_application(choice.path, storage_dir, application_id)
            repository.add_document(kind="application_cv", path=str(copied), checksum=choice.checksum)
            repository.update_application_cv_path(application_id, str(copied))
            queued += 1
        except (OSError, ValueError):
            # Keep processing other verified postings; the failed job remains
            # unqueued so the user can inspect it instead of losing the listing.
            continue
    return queued
