"""Run one fail-closed application using an injected browser adapter."""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
from uuid import uuid4

from sampoagent.agents.browser import FormInspection
from sampoagent.applications.field_resolver import FormField, resolve_application_fields
from sampoagent.applications.urls import is_safe_public_https_url, is_same_public_origin
from sampoagent.applications.workflow import ApplicationMode, can_submit, classify_question, record_submission_result
from sampoagent.jobs.matching import matches_preferences
from sampoagent.jobs.discovery import build_search_plan
from sampoagent.jobs.runner import is_relevant_job
from sampoagent.jobs.service import job_snapshot_hash


_CV_TERMS = ("cv", "resume", "curriculum vitae", "ansioluettelo", "attach resume")
_MAX_VERIFICATION_AGE = timedelta(hours=24)
_MAX_MULTISTEP_PAGES = 8


def _active_verified_job(job: dict[str, object]) -> bool:
    if job.get("verification_state") != "VERIFIED" or not is_safe_public_https_url(str(job.get("application_url", ""))):
        return False
    if not job.get("verified_snapshot_hash") or str(job.get("verified_snapshot_hash")) != job_snapshot_hash(job):
        return False
    verified_at = str(job.get("verified_at") or "")
    try:
        verified_time = datetime.fromisoformat(verified_at)
        if verified_time.tzinfo is None or datetime.now(timezone.utc) - verified_time.astimezone(timezone.utc) > _MAX_VERIFICATION_AGE:
            return False
    except ValueError:
        return False
    deadline = str(job.get("deadline") or "").strip()
    if deadline:
        try:
            if date.fromisoformat(deadline[:10]) < date.today():
                return False
        except ValueError:
            return False
    return True


def _form_signature(fields: tuple[FormField, ...]) -> str:
    value = [{
        "id": f.field_id, "name": f.name, "label": f.label, "group_label": f.group_label,
        "description": f.description, "source": f.source, "required": f.required,
        "kind": f.kind, "options": f.options, "autocomplete": f.autocomplete,
    } for f in fields]
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _matches_autopilot_role_scope(repository: object, job: dict[str, object]) -> bool:
    preferences = repository.preferences()
    source_id = job.get("source_id")
    if source_id is not None:
        source = repository.source(int(source_id))
        if not source or not source.get("enabled"):
            return False
        if str(source.get("capability", "")).casefold() == "scrapling public page" and not source.get("terms_reviewed"):
            return False
    plan = build_search_plan(
        facts=(),
        candidate_records={},
        targets=repository.target_occupations(),
        career_profiles=repository.rows("career_profiles"),
        preferences=preferences,
        sources=(),
        max_queries=0,
    )
    return is_relevant_job(job, plan=plan, preferences=preferences)


def _policy_state(
    repository: object,
    application: dict[str, object],
    job: dict[str, object],
    *,
    risk: str,
    answers_resolved: bool,
    review_approved: bool = False,
    exclude_current_reservation: bool = False,
) -> str | None:
    if repository.setting("automation_paused") == "true":
        return "PAUSED"
    if not _active_verified_job(job):
        return "BLOCKED"
    application_id = int(application["id"])
    if repository.has_other_application_for_job(int(job["id"]), application_id):
        return "BLOCKED"
    try:
        daily_limit = int(repository.setting("daily_limit") or "0")
        mode = ApplicationMode(repository.setting("application_mode") or "review_everything")
    except (ValueError, TypeError):
        return "BLOCKED"
    applied_today = max(0, repository.submissions_reserved_today() - int(exclude_current_reservation))
    allowed = can_submit(
        mode,
        dry_run=repository.setting("dry_run") != "false",
        risk=risk,
        applied_today=applied_today,
        daily_limit=daily_limit,
        autopilot_authorized=repository.autopilot_authorized(),
        within_scope=(
            matches_preferences(job=job, preferences=repository.preferences())
            and (mode != ApplicationMode.AUTOPILOT or _matches_autopilot_role_scope(repository, job))
        ),
        required_answers_resolved=answers_resolved,
        review_approved=review_approved,
        job_active=True,
        duplicate=False,
        captcha_detected=False,
        paused=False,
    )
    if allowed:
        return None
    if daily_limit <= applied_today:
        return "LIMIT_REACHED"
    return "BLOCKED"


def _wait_for_user(repository: object, application_id: int, status: str, reason: str) -> str:
    repository.update_application_status(application_id, status, reason[:300], queue_state="WAITING_USER")
    return status


def _process_multistep_application(
    repository: object,
    application_id: int,
    browser: object,
    preparation_token: str,
    submission_context_fingerprint: str,
    application_url: str,
    expected_job_snapshot_hash: str,
    initial_inspection: FormInspection,
) -> str:
    """Handle a narrow, Full-Autopilot-only multi-page application form."""
    try:
        mode = ApplicationMode(repository.setting("application_mode") or "review_everything")
    except ValueError:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The application mode changed; review the application controls.")
    if mode != ApplicationMode.AUTOPILOT:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "A multi-step package cannot be reviewed as one complete unit; no candidate data was entered.",
        )

    pages: list[dict[str, object]] = []
    answers: list[dict[str, object]] = []
    seen_pages: set[tuple[str, str]] = set()
    aggregate_risk = "LOW"
    current = initial_inspection
    final_signature = ""
    final_uploads: dict[str, dict[str, str]] = {}
    attachment = {"name": "", "sha256": ""}
    cv_path = str((repository.application(application_id) or {}).get("cv_path") or "")
    final_inspection = None

    for step_number in range(1, _MAX_MULTISTEP_PAGES + 1):
        if current.captcha_detected:
            repository.hold_for_captcha(
                application_id,
                detected_url=current.final_url or application_url,
                note="Browser detected an access challenge",
            )
            return "CAPTCHA_HOLD"
        if current.authentication_required:
            return _wait_for_user(repository, application_id, "NEEDS_AUTH", "Sign in to the employer site in the saved local browser session, then resume this application.")
        if repository.automation_scope_fingerprint() != submission_context_fingerprint:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "Candidate information or application scope changed between application pages; review before resuming.")
        if not is_same_public_origin(application_url, current.final_url):
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The application changed origin; no further form data was entered.")

        schema = current.schema
        if schema is None:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The next application page has no verifiable form snapshot.")
        if not schema.single_form_context:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "Multiple form contexts were detected; choose the intended application form manually.")
        if schema.action_url and not is_same_public_origin(application_url, schema.action_url):
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The form action points to a different origin.")
        if not current.fields:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "No accessible application fields were detected on a form page.")
        page_key = (current.final_url, schema.signature)
        if page_key in seen_pages:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The application repeated a form page; review the employer flow manually.")
        seen_pages.add(page_key)

        application = repository.application(application_id)
        current_job = repository.job(int(application["job_id"])) if application else None
        if (
            not application or not current_job
            or application.get("status") != "QUEUED"
            or application.get("queue_state") != "PREPARING"
            or job_snapshot_hash(current_job) != expected_job_snapshot_hash
            or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
        ):
            return "BLOCKED"

        file_fields = [field for field in current.fields if field.kind == "file"]
        if schema.has_next_step:
            if not current.next_step_control_ready:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "A unique Next/Continue control was not detected; no data was entered on this page.")
            if step_number == _MAX_MULTISTEP_PAGES:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The form exceeds the eight-page automation limit; review it manually.")
            if file_fields:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "CV and attachments are supported only on the final form page; no data was entered on this page.")
        elif not current.submit_control_ready:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "No unique final application submit control was detected.")

        field_risks = {
            field.field_id: classify_question(" ".join((field.group_label, field.label, field.description, field.name, field.autocomplete)))
            for field in current.fields
        }
        high_risk = [field_id for field_id, risk in field_risks.items() if risk == "HIGH"]
        if high_risk:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "High-risk or legal declaration requires your review: " + ", ".join(high_risk))
        page_risk = "MEDIUM" if any(risk == "MEDIUM" for risk in field_risks.values()) else "LOW"
        if page_risk == "MEDIUM":
            aggregate_risk = "MEDIUM"

        regular_fields = [field for field in current.fields if field.kind != "file"]
        resolution = resolve_application_fields(
            regular_fields,
            profile=repository.profile() or {},
            facts=repository.rows("facts"),
            answers=repository.answers(),
            records={kind: repository.candidate_records(kind) for kind in ("experience", "education", "certificate", "licence", "language", "availability", "preference")},
            country=str(current_job.get("country") or ""),
            employer=str(current_job.get("company") or ""),
        )
        if not resolution.ready:
            identifiers = (*resolution.needs_input, *resolution.conflicts)
            return _wait_for_user(repository, application_id, "NEEDS_USER", "Required or conflicting candidate answers need review: " + ", ".join(identifiers))

        selected_upload = None
        if file_fields:
            if any(field.allows_multiple_files for field in file_fields):
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The form requests multiple attachments; review the complete attachment set manually.")
            matching_uploads = [field for field in file_fields if any(term in field.label.casefold() for term in _CV_TERMS)]
            required_file_fields = [field for field in file_fields if field.required]
            if required_file_fields and (not cv_path or not Path(cv_path).is_file() or not matching_uploads):
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The final page requires a CV, but no uniquely identified upload field and readable archived CV are available.")
            if len(matching_uploads) > 1 or any(field not in matching_uploads for field in required_file_fields):
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The final page has an unsupported attachment requirement; review it manually.")
            if matching_uploads and cv_path and Path(cv_path).is_file():
                selected_upload = matching_uploads[0]

        prefilled_unresolved = []
        for field in regular_fields:
            existing_value = str(current.values.get(field.field_id, "")).strip()
            if field.kind == "checkbox" and existing_value.casefold() in {"no", "false", "0", "unchecked"}:
                continue
            if existing_value and field.field_id not in resolution.values:
                prefilled_unresolved.append(field.field_id)
        if prefilled_unresolved:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The employer page has unverified values in fields: " + ", ".join(prefilled_unresolved))

        expected_uploads: dict[str, dict[str, str]] = {}
        if selected_upload is not None:
            try:
                attachment = {
                    "name": Path(cv_path).name,
                    "sha256": sha256(Path(cv_path).read_bytes()).hexdigest(),
                }
            except OSError:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The archived CV could not be re-verified before upload.")
            expected_uploads[selected_upload.field_id] = dict(attachment)
        actual_uploads = {
            field_id: {"name": str(item.get("name", "")), "sha256": str(item.get("sha256", ""))}
            for field_id, item in current.uploaded_files.items()
        }
        if actual_uploads and actual_uploads != expected_uploads:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The employer page already contains an attachment outside the selected application package.")

        page_policy = _policy_state(repository, application, current_job, risk=page_risk, answers_resolved=True, review_approved=True)
        if page_policy == "PAUSED":
            return "PAUSED"
        if page_policy == "LIMIT_REACHED":
            return page_policy
        if page_policy:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "Full Autopilot authorization or job scope changed before this page; review before resuming.")

        try:
            for field in regular_fields:
                if repository.automation_scope_fingerprint() != submission_context_fingerprint:
                    return _wait_for_user(repository, application_id, "NEEDS_USER", "Candidate information changed before this page was filled.")
                application = repository.application(application_id)
                current_job = repository.job(int(application["job_id"])) if application else None
                if (
                    not application or not current_job
                    or application.get("status") != "QUEUED"
                    or application.get("queue_state") != "PREPARING"
                    or job_snapshot_hash(current_job) != expected_job_snapshot_hash
                    or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
                ):
                    return "BLOCKED"
                field_policy = _policy_state(repository, application, current_job, risk=page_risk, answers_resolved=True, review_approved=True)
                if field_policy == "PAUSED":
                    return "PAUSED"
                if field_policy == "LIMIT_REACHED":
                    return field_policy
                if field_policy:
                    return _wait_for_user(repository, application_id, "NEEDS_USER", "Full Autopilot authorization or job scope changed before a field was filled.")
                value = resolution.values.get(field.field_id)
                if value is not None:
                    browser.fill(field.field_id, value)
            if selected_upload is not None:
                if repository.automation_scope_fingerprint() != submission_context_fingerprint:
                    return _wait_for_user(repository, application_id, "NEEDS_USER", "Candidate information changed before the CV upload.")
                application = repository.application(application_id)
                current_job = repository.job(int(application["job_id"])) if application else None
                if (
                    not application or not current_job
                    or application.get("status") != "QUEUED"
                    or application.get("queue_state") != "PREPARING"
                    or job_snapshot_hash(current_job) != expected_job_snapshot_hash
                    or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
                ):
                    return "BLOCKED"
                upload_policy = _policy_state(repository, application, current_job, risk=page_risk, answers_resolved=True, review_approved=True)
                if upload_policy == "PAUSED":
                    return "PAUSED"
                if upload_policy == "LIMIT_REACHED":
                    return upload_policy
                if upload_policy:
                    return _wait_for_user(repository, application_id, "NEEDS_USER", "Full Autopilot authorization or job scope changed before the CV upload.")
                browser.upload(selected_upload.field_id, cv_path)
            errors = tuple(browser.validation_errors())
            after_fill = browser.inspect_form()
        except Exception:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The form changed or a field could not be safely filled.")

        if after_fill.captcha_detected:
            repository.hold_for_captcha(application_id, detected_url=after_fill.final_url or application_url, note="Access challenge appeared while preparing the form")
            return "CAPTCHA_HOLD"
        if after_fill.authentication_required:
            return _wait_for_user(repository, application_id, "NEEDS_AUTH", "The employer session expired while preparing the form.")
        if not is_same_public_origin(application_url, after_fill.final_url):
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The form redirected to an unexpected destination; no final application was submitted.")
        if errors or after_fill.validation_errors:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The employer form rejected one or more prepared values.")
        mismatched = [field_id for field_id, expected in resolution.values.items() if after_fill.values.get(field_id) != expected]
        if mismatched:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The browser could not verify prepared values in fields: " + ", ".join(mismatched))
        if after_fill.schema is None or after_fill.schema.signature != schema.signature:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The form changed after values were entered; review the new questions.")
        if after_fill.schema.has_next_step != schema.has_next_step:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The form navigation changed after values were entered.")
        if not schema.has_next_step and not after_fill.submit_control_ready:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The final application button changed after values were entered.")
        if selected_upload is not None:
            actual = after_fill.uploaded_files.get(selected_upload.field_id, {})
            identity = {"name": actual.get("name", ""), "sha256": actual.get("sha256", "")}
            if identity != expected_uploads[selected_upload.field_id]:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The selected CV attachment could not be verified.")

        pages.append({
            "signature": schema.signature,
            "checkpoint": schema.navigation_checkpoint,
            "fields": [
                {"id": field.field_id, "label": field.label, "required": field.required, "kind": field.kind}
                for field in current.fields
            ],
        })
        for field in regular_fields:
            answers.append({
                "step": step_number,
                "field_id": field.field_id,
                "label": field.label,
                "value": resolution.values.get(field.field_id),
                "source": resolution.sources.get(field.field_id, "not answered"),
            })

        final_inspection = after_fill
        final_signature = after_fill.signature or schema.signature
        final_uploads = expected_uploads
        if not schema.has_next_step:
            break

        transition_block: list[str] = []

        def recheck_before_advance() -> bool:
            current_application = repository.application(application_id)
            current_job = repository.job(int(current_application["job_id"])) if current_application else None
            if (
                repository.automation_scope_fingerprint() != submission_context_fingerprint
                or not current_application
                or current_application.get("status") != "QUEUED"
                or current_application.get("queue_state") != "PREPARING"
                or not current_job
                or job_snapshot_hash(current_job) != expected_job_snapshot_hash
                or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
            ):
                transition_block.append("BLOCKED")
                return False
            policy = _policy_state(repository, current_application, current_job, risk=aggregate_risk, answers_resolved=True, review_approved=True)
            if policy:
                transition_block.append(policy)
                return False
            return True

        try:
            current = browser.advance_step(expected_signature=final_signature, pre_click_check=recheck_before_advance)
        except Exception:
            if transition_block and transition_block[0] == "PAUSED":
                return "PAUSED"
            if transition_block and transition_block[0] == "LIMIT_REACHED":
                return "LIMIT_REACHED"
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The application step transition could not be verified; review the current employer page before resuming.")

    if final_inspection is None or not final_signature:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The application did not reach a verifiable final form page.")
    if final_inspection.schema and final_inspection.schema.has_next_step:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form exceeds the eight-page automation limit; review it manually.")

    application = repository.application(application_id)
    current_job = repository.job(int(application["job_id"])) if application else None
    if (
        not application or not current_job
        or application.get("status") != "QUEUED"
        or application.get("queue_state") != "PREPARING"
        or job_snapshot_hash(current_job) != expected_job_snapshot_hash
        or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
    ):
        return "BLOCKED"
    if repository.automation_scope_fingerprint() != submission_context_fingerprint:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "Candidate information or application scope changed before final submission.")
    manifest_signature = sha256(json.dumps(pages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    package = {
        "application": application_id,
        "job": {key: current_job.get(key) for key in ("id", "title", "company", "application_url", "deadline", "verification_state", "verified_at")},
        "form": {"signature": manifest_signature, "pages": pages},
        "answers": answers,
        "cv": attachment,
    }
    package_hash = sha256(json.dumps(package, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    final_policy = _policy_state(repository, application, current_job, risk=aggregate_risk, answers_resolved=True, review_approved=True)
    if final_policy == "PAUSED":
        return "PAUSED"
    if final_policy:
        return final_policy if final_policy == "LIMIT_REACHED" else _wait_for_user(repository, application_id, "NEEDS_USER", "Full Autopilot authorization changed before final submission.")
    attempt_id = repository.reserve_submission_attempt(
        application_id,
        daily_limit=int(repository.setting("daily_limit") or "0"),
        package_hash=package_hash,
        preparation_token=preparation_token,
    )
    if attempt_id is None:
        return "LIMIT_REACHED" if repository.submissions_reserved_today() >= int(repository.setting("daily_limit") or "0") else "NOT_READY"
    pre_click_block: list[str] = []

    def recheck_before_final_click() -> bool:
        current_application = repository.application(application_id)
        latest_job = repository.job(int(current_application["job_id"])) if current_application else None
        if (
            not current_application or not latest_job
            or int(current_application.get("job_id", -1)) != int(application["job_id"])
            or job_snapshot_hash(latest_job) != expected_job_snapshot_hash
            or current_application.get("status") != "SUBMITTING"
            or current_application.get("queue_state") != "SUBMITTING"
            or not repository.submission_attempt_is_active(application_id, attempt_id, package_hash=package_hash)
            or repository.automation_scope_fingerprint() != submission_context_fingerprint
        ):
            pre_click_block.append("BLOCKED")
            return False
        state = _policy_state(repository, current_application, latest_job, risk=aggregate_risk, answers_resolved=True, review_approved=True, exclude_current_reservation=True)
        if state:
            pre_click_block.append(state)
            return False
        return True

    try:
        result = browser.submit(
            application_url,
            expected_signature=final_signature,
            expected_uploads=final_uploads,
            pre_click_check=recheck_before_final_click,
        )
        if pre_click_block:
            if not repository.cancel_unsubmitted_attempt(application_id, message="Authorization or pause state changed before the final click"):
                raise RuntimeError("A pre-click cancellation could not be confirmed")
            if pre_click_block[0] == "PAUSED":
                return "PAUSED"
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The application authorization changed before final submission; review the controls.")
        if not result.submitted and not result.outcome_unknown and not result.captcha_detected and result.manual_action_required:
            if repository.cancel_unsubmitted_attempt(application_id, message="The browser stopped before clicking Submit; no external submission was made"):
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The browser stopped before final submission; review the form and resume when ready.")
        if result.final_url and not is_same_public_origin(application_url, result.final_url):
            raise ValueError("Final confirmation URL changed origin")
        return record_submission_result(repository, application_id, result)
    except Exception:
        repository.update_application_status(application_id, "SUBMITTED_UNVERIFIED", "Submission was attempted, but the result could not be safely recorded; automatic retry is disabled.", queue_state="DO_NOT_RETRY")
        repository.finish_submission_attempt(application_id, state="UNKNOWN", message="Submission result could not be safely recorded")
        return "SUBMITTED_UNVERIFIED"


def process_application(repository: object, application_id: int, browser: object) -> str:
    """Prepare, revalidate and make at most one authorized final-submit click.

    Any unknown factual field, high-risk question, access challenge, changed form,
    stale grant, or uncertain browser result blocks or pauses the workflow.
    """
    application = repository.application(application_id)
    if not application or application.get("status") != "QUEUED" or application.get("queue_state") != "READY":
        return "NOT_READY"
    if not getattr(browser, "configured", False):
        return "BROWSER_UNAVAILABLE"
    preparation_token = uuid4().hex
    if not repository.claim_application_preparation(application_id, owner_token=preparation_token):
        return "NOT_READY"
    try:
        return _process_claimed_application(repository, application_id, browser, preparation_token)
    finally:
        repository.release_application_preparation(application_id, owner_token=preparation_token)


def _process_claimed_application(repository: object, application_id: int, browser: object, preparation_token: str) -> str:
    """Run a form preparation only while this process still owns its DB fence."""
    application = repository.application(application_id)
    if (
        not application or application.get("status") != "QUEUED"
        or application.get("queue_state") != "PREPARING"
        or application.get("preparation_token") != preparation_token
    ):
        return "NOT_READY"
    job = repository.job(int(application["job_id"]))
    if not job:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The linked job no longer exists.")
    try:
        mode = ApplicationMode(repository.setting("application_mode") or "review_everything")
    except ValueError:
        return "BLOCKED"
    initial_policy = _policy_state(
        repository,
        application,
        job,
        risk="LOW",
        answers_resolved=True,
        review_approved=mode == ApplicationMode.SMART_APPROVAL,
    )
    if initial_policy:
        return initial_policy
    submission_context_fingerprint = repository.automation_scope_fingerprint()

    application_url = str(job.get("application_url", ""))
    try:
        browser.open(application_url)
        inspection = browser.inspect_form()
    except Exception:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The application page could not be inspected safely.")
    if inspection.captcha_detected:
        repository.hold_for_captcha(application_id, detected_url=application_url, note="Browser detected an access challenge")
        return "CAPTCHA_HOLD"
    if inspection.authentication_required:
        return _wait_for_user(repository, application_id, "NEEDS_AUTH", "Sign in to the employer site in the saved local browser session, then resume this application.")
    if repository.automation_scope_fingerprint() != submission_context_fingerprint:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "Candidate information or application scope changed while the form was loading; no candidate data was entered.",
        )
    if not is_same_public_origin(application_url, inspection.final_url):
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The employer page redirected to an unexpected destination; no candidate data was entered.")
    schema = inspection.schema
    if schema and not schema.single_form_context:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "Multiple form contexts were detected; choose the intended application form manually.")
    if schema and schema.action_url and not is_same_public_origin(application_url, schema.action_url):
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form submission action points to a different origin; no candidate data was entered.")
    if schema and schema.has_next_step:
        return _process_multistep_application(
            repository,
            application_id,
            browser,
            preparation_token,
            submission_context_fingerprint,
            application_url,
            job_snapshot_hash(job),
            inspection,
        )
    if not inspection.submit_control_ready:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "No unique application submit control was detected.")
    if not inspection.fields:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "No accessible application fields were detected.")

    field_risks = {
        field.field_id: classify_question(" ".join((field.group_label, field.label, field.description, field.name, field.autocomplete)))
        for field in inspection.fields
    }
    high_risk = [field_id for field_id, risk in field_risks.items() if risk == "HIGH"]
    if high_risk:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "High-risk or legal declaration requires your review: " + ", ".join(high_risk))
    form_risk = "MEDIUM" if any(risk == "MEDIUM" for risk in field_risks.values()) else "LOW"

    file_fields = [field for field in inspection.fields if field.kind == "file"]
    if any(field.allows_multiple_files for field in file_fields):
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "The application form requests a multi-file attachment set; choose and review every required file manually.",
        )
    regular_fields = [field for field in inspection.fields if field.kind != "file"]
    resolution = resolve_application_fields(
        regular_fields,
        profile=repository.profile() or {},
        facts=repository.rows("facts"),
        answers=repository.answers(),
        records={kind: repository.candidate_records(kind) for kind in ("experience", "education", "certificate", "licence", "language", "availability", "preference")},
        country=str(job.get("country") or ""),
        employer=str(job.get("company") or ""),
    )
    if not resolution.ready:
        identifiers = (*resolution.needs_input, *resolution.conflicts)
        return _wait_for_user(repository, application_id, "NEEDS_USER", "Required or conflicting candidate answers need review: " + ", ".join(identifiers))

    cv_path = str(application.get("cv_path") or "")
    matching_uploads = [field for field in file_fields if any(term in field.label.casefold() for term in _CV_TERMS)]
    required_file_fields = [field for field in file_fields if field.required]
    if required_file_fields and (not cv_path or not Path(cv_path).is_file() or not matching_uploads):
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form requires a CV file, but no uniquely identifiable upload field and readable archived CV are available.")
    if len(matching_uploads) > 1:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form has multiple possible CV upload fields; choose one manually.")
    missing_required_uploads = [field for field in required_file_fields if field not in matching_uploads]
    if missing_required_uploads:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "The form requires an additional attachment that SampoAgent cannot safely select; review the required files manually.",
        )
    if matching_uploads and cv_path and Path(cv_path).is_file():
        selected_upload = matching_uploads[0]
    else:
        selected_upload = None

    prefilled_unresolved = []
    for field in regular_fields:
        existing_value = str(inspection.values.get(field.field_id, "")).strip()
        if field.kind == "checkbox" and existing_value.casefold() in {"no", "false", "0", "unchecked"}:
            continue
        if existing_value and field.field_id not in resolution.values:
            prefilled_unresolved.append(field.field_id)
    if prefilled_unresolved:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "The employer page already contains unverified values in fields: " + ", ".join(prefilled_unresolved),
        )

    expected_initial_uploads: dict[str, dict[str, str]] = {}
    if selected_upload is not None:
        expected_initial_uploads[selected_upload.field_id] = {
            "name": Path(cv_path).name,
            "sha256": sha256(Path(cv_path).read_bytes()).hexdigest(),
        }
    actual_initial_uploads = {
        field_id: {"name": str(upload.get("name", "")), "sha256": str(upload.get("sha256", ""))}
        for field_id, upload in inspection.uploaded_files.items()
    }
    if actual_initial_uploads and actual_initial_uploads != expected_initial_uploads:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "The employer page already contains an attachment that is not part of the selected application package.",
        )

    before_signature = inspection.signature or _form_signature(inspection.fields)
    current_job = repository.job(int(application["job_id"]))
    current_application = repository.application(application_id)
    if not current_job or not current_application or current_application.get("status") != "QUEUED" or current_application.get("queue_state") != "PREPARING" or not repository.owns_application_preparation(application_id, owner_token=preparation_token):
        return "BLOCKED"
    review_approved = False
    attachment = {"name": "", "sha256": ""}
    if selected_upload is not None:
        attachment = {
            "name": Path(cv_path).name,
            "sha256": sha256(Path(cv_path).read_bytes()).hexdigest(),
        }
    package = {
        "application": application_id,
        "job": {key: current_job.get(key) for key in ("id", "title", "company", "application_url", "deadline", "verification_state", "verified_at")},
        "form": {
            "signature": before_signature,
            "fields": [
                {"id": field.field_id, "label": field.label, "required": field.required, "kind": field.kind}
                for field in regular_fields
            ],
        },
        "answers": [
            {
                "field_id": field.field_id,
                "label": field.label,
                "value": resolution.values.get(field.field_id),
                "source": resolution.sources.get(field.field_id, "not answered"),
            }
            for field in regular_fields
        ],
        "cv": attachment,
    }
    package_hash = sha256(json.dumps(package, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    try:
        mode = ApplicationMode(repository.setting("application_mode") or "review_everything")
    except ValueError:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The application mode changed while the employer page was loading; review the application controls.")
    current_job = repository.job(int(application["job_id"]))
    current_application = repository.application(application_id)
    if (
        not current_job
        or not current_application
        or current_application.get("status") != "QUEUED"
        or current_application.get("queue_state") != "PREPARING"
        or not repository.owns_application_preparation(application_id, owner_token=preparation_token)
    ):
        return "BLOCKED"
    # Recheck live authorization/scope before any candidate value is sent to
    # the employer page. Smart Approval's exact package approval is checked
    # separately below, after binding the package hash.
    entry_policy = _policy_state(
        repository,
        current_application,
        current_job,
        risk=form_risk,
        answers_resolved=True,
        review_approved=True,
    )
    if entry_policy == "PAUSED":
        return "PAUSED"
    if entry_policy == "LIMIT_REACHED":
        return entry_policy
    if entry_policy:
        return _wait_for_user(
            repository,
            application_id,
            "NEEDS_USER",
            "Automation authorization or application scope changed while the form was loading; no candidate data was entered.",
        )
    if mode == ApplicationMode.SMART_APPROVAL:
        review_approved = repository.application_review_approved(application_id, package_hash)
        if not review_approved:
            repository.save_application_review(application_id, package_hash=package_hash, package=package)
            return "NEEDS_REVIEW"

    try:
        for field in regular_fields:
            if repository.automation_scope_fingerprint() != submission_context_fingerprint:
                return _wait_for_user(
                    repository,
                    application_id,
                    "NEEDS_USER",
                    "Candidate information or application scope changed while preparing the form; review it before resuming.",
                )
            value = resolution.values.get(field.field_id)
            if value is not None:
                browser.fill(field.field_id, value)
        if selected_upload is not None:
            if repository.automation_scope_fingerprint() != submission_context_fingerprint:
                return _wait_for_user(
                    repository,
                    application_id,
                    "NEEDS_USER",
                    "Candidate information or application scope changed before the CV upload; review it before resuming.",
                )
            browser.upload(selected_upload.field_id, cv_path)
        # Native required inputs are expected to be invalid before confirmed
        # answers and the selected CV have been entered. Validate completeness
        # only after that bounded fill phase; unresolved answers already stop above.
        errors = tuple(browser.validation_errors())
        after_fill = browser.inspect_form()
    except Exception:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form changed or a field could not be safely filled.")
    if after_fill.captcha_detected:
        repository.hold_for_captcha(application_id, detected_url=application_url, note="Access challenge appeared while preparing the form")
        return "CAPTCHA_HOLD"
    if after_fill.authentication_required:
        return _wait_for_user(repository, application_id, "NEEDS_AUTH", "The employer session expired while preparing the form.")
    if not is_same_public_origin(application_url, after_fill.final_url):
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form redirected to an unexpected destination; the final application was not submitted.")
    if errors or after_fill.validation_errors:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The employer form rejected one or more prepared values.")
    mismatched_fields = [
        field_id
        for field_id, expected in resolution.values.items()
        if after_fill.values.get(field_id) != expected
    ]
    if mismatched_fields:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The browser could not verify the prepared values in fields: " + ", ".join(mismatched_fields))
    if not after_fill.submit_control_ready:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form no longer has a unique application submit control.")
    after_signature = after_fill.signature or _form_signature(after_fill.fields)
    if before_signature != after_signature:
        return _wait_for_user(repository, application_id, "NEEDS_USER", "The form changed after values were entered; review the new questions.")

    current_job = repository.job(int(application["job_id"]))
    current_application = repository.application(application_id)
    if not current_job or not current_application or current_application.get("status") != "QUEUED" or current_application.get("queue_state") != "PREPARING" or not repository.owns_application_preparation(application_id, owner_token=preparation_token):
        return "BLOCKED"
    final_attachment = attachment
    if selected_upload is not None:
        try:
            final_attachment = {
                "name": Path(cv_path).name,
                "sha256": sha256(Path(cv_path).read_bytes()).hexdigest(),
            }
        except OSError:
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The archived CV could not be re-verified before submission.")
    final_package = {
        "application": application_id,
        "job": {key: current_job.get(key) for key in ("id", "title", "company", "application_url", "deadline", "verification_state", "verified_at")},
        "form": {
            "signature": after_signature,
            "fields": [
                {"id": field.field_id, "label": field.label, "required": field.required, "kind": field.kind}
                for field in regular_fields
            ],
        },
        "answers": package["answers"],
        "cv": final_attachment,
    }
    final_package_hash = sha256(json.dumps(final_package, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if selected_upload is not None:
        uploaded = after_fill.uploaded_files.get(selected_upload.field_id, {})
        uploaded_identity = {"name": uploaded.get("name", ""), "sha256": uploaded.get("sha256", "")}
        if uploaded_identity not in (attachment, final_attachment):
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The browser could not verify the selected CV attachment checksum.")
    if final_package_hash != package_hash:
        if mode == ApplicationMode.SMART_APPROVAL:
            repository.save_application_review(application_id, package_hash=final_package_hash, package=final_package)
            return "NEEDS_REVIEW"
        return _wait_for_user(repository, application_id, "NEEDS_REVIEW" if mode == ApplicationMode.SMART_APPROVAL else "NEEDS_USER", "The application package changed after preparation; review it again.")
    final_review_approved = mode != ApplicationMode.SMART_APPROVAL or repository.application_review_approved(application_id, package_hash)
    final_policy = _policy_state(
        repository,
        current_application,
        current_job,
        risk=form_risk,
        answers_resolved=True,
        review_approved=final_review_approved,
    )
    if final_policy:
        return final_policy
    attempt_id = repository.reserve_submission_attempt(
        application_id,
        daily_limit=int(repository.setting("daily_limit") or "0"),
        package_hash=package_hash,
        preparation_token=preparation_token,
    )
    if attempt_id is None:
        return "LIMIT_REACHED" if repository.submissions_reserved_today() >= int(repository.setting("daily_limit") or "0") else "NOT_READY"
    pre_click_block: list[str] = []

    def recheck_before_click() -> bool:
        current_job_state = repository.job(int(application["job_id"]))
        current_application_state = repository.application(application_id)
        if (
            not current_job_state or not current_application_state
            or current_application_state.get("status") != "SUBMITTING"
            or current_application_state.get("queue_state") != "SUBMITTING"
            or not repository.submission_attempt_is_active(application_id, attempt_id, package_hash=package_hash)
        ):
            pre_click_block.append("BLOCKED")
            return False
        if repository.automation_scope_fingerprint() != submission_context_fingerprint:
            pre_click_block.append("BLOCKED")
            return False
        try:
            current_mode = ApplicationMode(repository.setting("application_mode") or "review_everything")
        except ValueError:
            pre_click_block.append("BLOCKED")
            return False
        current_review_approved = (
            current_mode != ApplicationMode.SMART_APPROVAL
            or repository.application_review_approved(application_id, package_hash)
        )
        blocked = _policy_state(
            repository,
            current_application_state,
            current_job_state,
            risk=form_risk,
            answers_resolved=True,
            review_approved=current_review_approved,
            exclude_current_reservation=True,
        )
        if blocked:
            pre_click_block.append(blocked)
            return False
        return True

    try:
        expected_uploads = (
            {
                selected_upload.field_id: {
                    "name": final_attachment["name"],
                    "sha256": final_attachment["sha256"],
                }
            }
            if selected_upload is not None
            else {}
        )
        result = browser.submit(
            application_url,
            expected_signature=after_signature,
            expected_uploads=expected_uploads,
            pre_click_check=recheck_before_click,
        )
        if pre_click_block:
            cancelled = repository.cancel_unsubmitted_attempt(
                application_id,
                message="Authorization or pause state changed before the final click",
            )
            if not cancelled:
                raise RuntimeError("A pre-click cancellation could not be confirmed")
            reason = pre_click_block[0]
            if reason == "PAUSED":
                return "PAUSED"
            return _wait_for_user(repository, application_id, "NEEDS_USER", "The application authorization changed before submission; review the current automation settings and application again.")
        if not result.submitted and not result.outcome_unknown and not result.captcha_detected and result.manual_action_required:
            cancelled = repository.cancel_unsubmitted_attempt(
                application_id,
                message="The browser stopped before clicking Submit; no external submission was made",
            )
            if cancelled:
                return _wait_for_user(repository, application_id, "NEEDS_USER", "The browser stopped before submission; review the form and resume when ready.")
        if result.final_url and not is_same_public_origin(application_url, result.final_url):
            raise ValueError("Final confirmation URL changed to an unexpected origin")
        return record_submission_result(repository, application_id, result)
    except Exception:
        repository.update_application_status(application_id, "SUBMITTED_UNVERIFIED", "Submission was attempted, but the result could not be safely recorded; automatic retry is disabled.", queue_state="DO_NOT_RETRY")
        repository.finish_submission_attempt(application_id, state="UNKNOWN", message="Submission result could not be safely recorded")
        return "SUBMITTED_UNVERIFIED"
