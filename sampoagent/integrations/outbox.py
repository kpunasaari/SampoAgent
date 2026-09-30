"""Encrypted, single-application email drafts with candidate-confirmed recipients."""

from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
import json
from pathlib import Path
import re
from collections.abc import Callable
from datetime import datetime, timezone

from sampoagent.applications.runner import _active_verified_job, _matches_autopilot_role_scope
from sampoagent.jobs.matching import matches_preferences
from sampoagent.integrations.email_oauth import (
    EmailIntegrationError,
    OAuthConfig,
    decrypt_token_payload,
    encrypt_token_payload,
    refresh_access_token,
    required_email_scope,
)
from sampoagent.integrations.email_recipient import extract_application_email_recipient
from sampoagent.integrations.email_send import EmailSendResult, send_email_message


class EmailOutboxError(ValueError):
    """Safe validation error for preparing a local application email."""


_MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


def _single_line(value: object, *, maximum: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _validated_recipient(value: str) -> str:
    recipient = value.strip()
    parsed = parseaddr(recipient)
    if (
        not recipient or len(recipient) > 320 or parsed[1] != recipient
        or any(char in recipient for char in "\r\n,;<>\x00")
        or recipient.count("@") != 1
    ):
        raise EmailOutboxError("Enter one email address copied from the current employer listing.")
    local, domain = recipient.rsplit("@", 1)
    if not local or not domain or "." not in domain or any(char.isspace() for char in recipient):
        raise EmailOutboxError("Enter one valid employer email address.")
    return recipient


def _application_cv(repository: object, storage_dir: Path, application_id: int) -> tuple[Path, bytes, str]:
    application = repository.application(application_id)
    if not application or not application.get("cv_path"):
        raise EmailOutboxError("Prepare and verify a CV for this application before creating its email.")
    managed_root = (storage_dir / "applications" / str(application_id)).resolve()
    path = Path(str(application["cv_path"]))
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(managed_root)
    except (OSError, ValueError):
        raise EmailOutboxError("The application's archived CV copy is unavailable; prepare the package again.") from None
    if path.is_symlink() or not resolved.is_file() or resolved.suffix.casefold() != ".pdf":
        raise EmailOutboxError("The application's attachment is not a supported archived PDF CV.")
    if resolved.stat().st_size <= 0 or resolved.stat().st_size > _MAX_ATTACHMENT_BYTES:
        raise EmailOutboxError("The archived CV must be between 1 byte and 2 MiB for email submission.")
    content = resolved.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise EmailOutboxError("The archived CV does not have a valid PDF signature.")
    return resolved, content, sha256(content).hexdigest()


def _email_copy(language: str, *, role: str, company: str, name: str) -> tuple[str, str]:
    safe_role = _single_line(role, maximum=140)
    safe_company = _single_line(company, maximum=140)
    safe_name = _single_line(name, maximum=140)
    if not safe_role or not safe_company or not safe_name:
        raise EmailOutboxError("The candidate name and reviewed job title and employer are required.")
    if language == "fi":
        subject = f"Työhakemus: {safe_role} — {safe_name}"
        body = (
            f"Hei rekrytointitiimi,\n\nHaen tehtävää {safe_role} yrityksessä {safe_company}. "
            "Ansioluetteloni on liitteenä. Kerron mielelläni lisää hakemuksestani.\n\n"
            f"Ystävällisin terveisin,\n{safe_name}\n"
        )
    elif language == "sv":
        subject = f"Ansökan: {safe_role} — {safe_name}"
        body = (
            f"Hej rekryteringsteamet,\n\nJag ansöker till tjänsten {safe_role} hos {safe_company}. "
            "Mitt CV finns bifogat. Jag berättar gärna mer om min ansökan.\n\n"
            f"Vänliga hälsningar,\n{safe_name}\n"
        )
    else:
        subject = f"Application: {safe_role} — {safe_name}"
        body = (
            f"Dear recruitment team,\n\nI am applying for the {safe_role} position at {safe_company}. "
            "My CV is attached. I would welcome the opportunity to discuss my application.\n\n"
            f"Kind regards,\n{safe_name}\n"
        )
    return subject, body


def create_application_email_draft(
    repository: object,
    storage_dir: Path,
    application_id: int,
    *,
    recipient: str,
    confirm_recipient_from_posting: bool,
) -> dict[str, object]:
    """Create one encrypted draft. This only writes local state; it never sends."""
    if not confirm_recipient_from_posting:
        raise EmailOutboxError("Confirm that you copied the recipient from the current employer posting.")
    recipient = _validated_recipient(recipient)
    application = repository.application(application_id)
    job = repository.job(int(application["job_id"])) if application else None
    if not job or not _active_verified_job(job):
        raise EmailOutboxError("Recheck the current public job listing before creating an email application.")
    return _create_application_email_package(
        repository, storage_dir, application_id, recipient=recipient,
        recipient_source="candidate_confirmed_current_listing", cue_id="candidate_confirmation",
        verified_snapshot_hash=str(job.get("verified_snapshot_hash", "")), description_sha256="",
    )


def _create_application_email_package(
    repository: object,
    storage_dir: Path,
    application_id: int,
    *,
    recipient: str,
    recipient_source: str,
    cue_id: str,
    verified_snapshot_hash: str,
    description_sha256: str,
) -> dict[str, object]:
    recipient = _validated_recipient(recipient)
    application = repository.application(application_id)
    if not application or application["status"] != "QUEUED" or application["queue_state"] != "READY":
        raise EmailOutboxError("Only a prepared, not-yet-submitted application can create an email draft.")
    job = repository.job(int(application["job_id"]))
    if not _active_verified_job(job or {}):
        raise EmailOutboxError("Recheck the current public job listing before creating an email application.")
    if str(job.get("verified_snapshot_hash", "")) != verified_snapshot_hash:
        raise EmailOutboxError("The recipient evidence does not match the latest verified listing snapshot.")
    if recipient_source == "verified_listing":
        evidence = extract_application_email_recipient(
            str(job.get("description", "")), language=str(job.get("language", "")),
            verified_snapshot_hash=verified_snapshot_hash,
        )
        if (
            evidence.status != "READY" or evidence.recipient is None
            or not hmac_compare(evidence.recipient.casefold(), recipient.casefold())
            or evidence.cue_id != cue_id or evidence.description_sha256 != description_sha256
        ):
            raise EmailOutboxError("The recipient must come from one explicit application address in the current verified listing.")
    elif recipient_source != "candidate_confirmed_current_listing" or cue_id != "candidate_confirmation":
        raise EmailOutboxError("The recipient evidence source is not supported.")
    connection = repository.mail_send_connection()
    if not connection:
        raise EmailOutboxError("Connect a separate email-send account before preparing an email application.")
    cv_path, cv_bytes, cv_checksum = _application_cv(repository, storage_dir, application_id)
    candidate = repository.profile()
    if not candidate:
        raise EmailOutboxError("Create and review the candidate profile before preparing an email application.")
    subject, body = _email_copy(
        "sv" if cue_id.startswith("sv_") else str(job.get("language", "en")),
        role=str(job.get("title", "")),
        company=str(job.get("company", "")),
        name=str(candidate.get("name", "")),
    )
    package = {
        "application_id": application_id,
        "provider": str(connection["provider"]),
        "sender_account_id": int(connection["id"]),
        "sender_subject": str(connection["provider_subject"]),
        "sender_email": str(connection["sender_email"]),
        "sender_address_status": str(connection["address_status"]),
        "send_connection_connected_at": str(connection["connected_at"]),
        "language": "sv" if cue_id.startswith("sv_") else str(job.get("language", "en")),
        "role": str(job["title"]),
        "employer": str(job["company"]),
        "recipient": recipient,
        "recipient_source": recipient_source,
        "recipient_cue_id": cue_id,
        "verified_snapshot_hash": verified_snapshot_hash,
        "recipient_description_sha256": description_sha256,
        "subject": subject,
        "body": body,
        "attachment_path": str(cv_path),
        "attachment_name": cv_path.name,
        "attachment_sha256": cv_checksum,
        "job_fingerprint": str(job["fingerprint"]),
        "candidate_scope_fingerprint": repository.automation_scope_fingerprint(),
    }
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package_hash = sha256(canonical.encode("utf-8")).hexdigest()
    payload_ciphertext = encrypt_token_payload(package)
    try:
        return repository.create_email_outbox(
            application_id=application_id,
            provider=str(connection["provider"]),
            sender_account_id=int(connection["id"]),
            package_hash=package_hash,
            idempotency_key=sha256(f"application-email:{application_id}:{package_hash}".encode()).hexdigest(),
            payload_ciphertext=payload_ciphertext,
        )
    except EmailIntegrationError:
        raise EmailOutboxError("Email encryption is not configured; no email draft was stored.") from None
    except ValueError:
        raise EmailOutboxError("An email draft already exists for this application; review or cancel it first.") from None


def prepare_autopilot_email_application(repository: object, storage_dir: Path, application_id: int) -> str:
    """Prepare an explicit verified-listing email route or safely hold it.

    Return NOT_EMAIL for normal browser applications, READY after one local
    outbox was prepared, or HELD when an email route needs user review.
    """
    application = repository.application(application_id)
    if not application or application.get("status") != "QUEUED" or application.get("queue_state") != "READY":
        return "NOT_READY"
    job = repository.job(int(application["job_id"]))
    if not job:
        return "NOT_EMAIL"
    evidence = extract_application_email_recipient(
        str(job.get("description", "")),
        language=str(job.get("language", "")),
        verified_snapshot_hash=str(job.get("verified_snapshot_hash", "")),
    )
    if evidence.status == "NOT_EMAIL_APPLICATION":
        return "NOT_EMAIL"
    if evidence.status != "READY" or not _active_verified_job(job):
        repository.update_application_status(
            application_id,
            "NEEDS_REVIEW",
            f"Email application held for review: {evidence.status if evidence.status != 'READY' else 'LISTING_NOT_CURRENT'}.",
            queue_state="WAITING_USER",
        )
        return "HELD"
    if (
        repository.setting("application_mode") != "autopilot"
        or not repository.autopilot_authorized()
        or not repository.email_send_autopilot_authorized()
        or repository.setting("dry_run") != "false"
        or repository.setting("automation_paused") == "true"
    ):
        repository.update_application_status(
            application_id, "NEEDS_REVIEW",
            "Email application held: Full Autopilot and separate email-send authorization are both required.",
            queue_state="WAITING_USER",
        )
        return "HELD"
    try:
        in_scope = matches_preferences(job=job, preferences=repository.effective_search_preferences()) and _matches_autopilot_role_scope(repository, job)
    except (TypeError, ValueError):
        in_scope = False
    if not in_scope:
        repository.update_application_status(
            application_id, "NEEDS_REVIEW", "Email application held: the current role or saved job preferences do not include this posting.",
            queue_state="WAITING_USER",
        )
        return "HELD"
    if not repository.mail_send_connection():
        repository.update_application_status(
            application_id, "NEEDS_REVIEW", "Email application held: select and connect a sender account.",
            queue_state="WAITING_USER",
        )
        return "HELD"
    try:
        _create_application_email_package(
            repository, storage_dir, application_id,
            recipient=str(evidence.recipient), recipient_source="verified_listing",
            cue_id=evidence.cue_id, verified_snapshot_hash=evidence.verified_snapshot_hash,
            description_sha256=evidence.description_sha256,
        )
    except (EmailOutboxError, EmailIntegrationError):
        repository.update_application_status(
            application_id, "NEEDS_REVIEW",
            "Email application held: its account, verified listing or archived CV package needs review.",
            queue_state="WAITING_USER",
        )
        return "HELD"
    return "READY"


def _verified_email_package(repository: object, storage_dir: Path, application_id: int, package_hash: str) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    outbox = repository.email_outbox_for_application(application_id)
    if not outbox or outbox.get("state") != "READY" or not hmac_compare(str(outbox.get("package_hash", "")), package_hash):
        raise EmailOutboxError("This email draft is no longer ready or its reviewed package changed.")
    ciphertext = repository.email_outbox_payload_ciphertext(application_id)
    if not ciphertext:
        raise EmailOutboxError("The encrypted email draft is unavailable.")
    package = decrypt_token_payload(ciphertext)
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not hmac_compare(sha256(canonical.encode("utf-8")).hexdigest(), package_hash):
        raise EmailOutboxError("This email draft's encrypted contents no longer match the reviewed package.")
    try:
        sender_account_id = int(package.get("sender_account_id", 0))
    except (TypeError, ValueError):
        sender_account_id = 0
    if sender_account_id <= 0 or int(outbox.get("sender_account_id") or 0) != sender_account_id:
        raise EmailOutboxError("This legacy email draft has no bound sender identity; prepare it again after reconnecting.")
    application = repository.application(application_id)
    if not application or application.get("status") != "QUEUED" or application.get("queue_state") != "EMAIL_READY":
        raise EmailOutboxError("The application is no longer eligible for an email send.")
    job = repository.job(int(application["job_id"]))
    if not job or not _active_verified_job(job) or not hmac_compare(str(package.get("job_fingerprint", "")), str(job.get("fingerprint", ""))):
        raise EmailOutboxError("The verified job listing changed or expired; review it again before sending.")
    if not hmac_compare(str(package.get("candidate_scope_fingerprint", "")), repository.automation_scope_fingerprint()):
        raise EmailOutboxError("Candidate facts, answers, or saved application scope changed; prepare and review the email again.")
    connection = repository.mail_send_connection(sender_account_id) if sender_account_id > 0 else None
    default_connection = repository.mail_send_connection()
    if (
        not connection or connection.get("provider") != package.get("provider")
        or int(connection.get("id", 0)) != sender_account_id
        or connection.get("provider_subject") != package.get("sender_subject")
        or connection.get("sender_email") != package.get("sender_email")
        or connection.get("address_status") != package.get("sender_address_status")
        or connection.get("connected_at") != package.get("send_connection_connected_at")
        or not default_connection or int(default_connection.get("id", 0)) != sender_account_id
    ):
        raise EmailOutboxError("The separate email-send account changed; review a newly prepared email draft.")
    scope = required_email_scope(str(connection["provider"]), "send")
    if scope not in str(connection.get("granted_scopes", "")).split():
        raise EmailOutboxError("The connected account has no separate send-only permission.")
    if not {"openid", "email"}.issubset(set(str(connection.get("granted_scopes", "")).split())):
        raise EmailOutboxError("The connected account identity permission changed; reconnect and review the package.")
    if str(package.get("verified_snapshot_hash", "")) != str(job.get("verified_snapshot_hash", "")):
        raise EmailOutboxError("The verified listing snapshot changed; review the application again.")
    if package.get("recipient_source") == "verified_listing":
        evidence = extract_application_email_recipient(
            str(job.get("description", "")), language=str(job.get("language", "")),
            verified_snapshot_hash=str(job.get("verified_snapshot_hash", "")),
        )
        if (
            evidence.status != "READY"
            or not hmac_compare(str(evidence.recipient or "").casefold(), str(package.get("recipient", "")).casefold())
            or evidence.cue_id != package.get("recipient_cue_id")
            or not hmac_compare(evidence.description_sha256, str(package.get("recipient_description_sha256", "")))
        ):
            raise EmailOutboxError("The verified listing no longer yields this exact application recipient.")
    cv_path, cv_bytes, cv_checksum = _application_cv(repository, storage_dir, application_id)
    if (
        str(package.get("attachment_path", "")) != str(cv_path)
        or str(package.get("attachment_name", "")) != cv_path.name
        or not hmac_compare(str(package.get("attachment_sha256", "")), cv_checksum)
    ):
        raise EmailOutboxError("The archived CV changed after review; no email was sent.")
    package["_attachment_bytes"] = cv_bytes
    return outbox, package, application, connection


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return bool(left and right and hmac.compare_digest(left, right))


def send_approved_application_email(
    repository: object,
    storage_dir: Path,
    application_id: int,
    *,
    package_hash: str,
    confirmed: bool,
    sender: Callable[..., EmailSendResult] = send_email_message,
    autopilot: bool = False,
) -> EmailSendResult:
    """Submit one individually or scoped-autopilot-authorized email; never retry ambiguity."""
    if not autopilot and not confirmed:
        raise EmailOutboxError("Confirm that you reviewed this exact email and attachment before sending.")
    try:
        outbox, package, application, connection = _verified_email_package(
            repository, storage_dir, application_id, package_hash
        )
    except EmailIntegrationError:
        raise EmailOutboxError("The encrypted send permission or email draft cannot be opened. Reconnect or prepare it again.") from None

    if repository.setting("dry_run") != "false" or repository.setting("automation_paused") == "true":
        raise EmailOutboxError("Dry Run is on or automation is paused; no email was sent.")
    try:
        daily_limit = int(repository.setting("daily_limit") or "0")
    except (ValueError, TypeError):
        daily_limit = 0
    if daily_limit <= 0:
        raise EmailOutboxError("Set a positive daily application limit before sending.")
    if repository.setting("application_mode") == "autopilot" and not repository.autopilot_authorized():
        raise EmailOutboxError("The Full Autopilot authorization is missing or expired.")
    if autopilot and not repository.email_send_autopilot_authorized():
        raise EmailOutboxError("Separate Full Autopilot email-send permission is missing or expired.")
    if autopilot and package.get("recipient_source") != "verified_listing":
        raise EmailOutboxError("Full Autopilot sends only to an unambiguous recipient taken from the verified current listing.")
    if autopilot:
        job = repository.job(int(application["job_id"]))
        try:
            in_scope = bool(job) and matches_preferences(job=job, preferences=repository.effective_search_preferences()) and _matches_autopilot_role_scope(repository, job)
        except (TypeError, ValueError):
            in_scope = False
        if not in_scope:
            raise EmailOutboxError("The verified email application is outside the current saved role or job preferences.")

    try:
        tokens = decrypt_token_payload(repository.mail_send_ciphertext(int(connection["id"])) or "")
        if float(tokens.get("expires_at", 0)) <= __import__("time").time() + 60:
            refresh_token = str(tokens.get("refresh_token", ""))
            if not refresh_token:
                raise EmailIntegrationError("Reconnect email send permission.")
            config = OAuthConfig.from_environment(str(connection["provider"]))
            tokens = refresh_access_token(config, refresh_token=refresh_token, purpose="send")
            tokens["expires_at"] = __import__("time").time() + float(tokens.get("expires_in", 3600))
            expires_at = datetime.fromtimestamp(float(tokens["expires_at"]), timezone.utc).isoformat()
            repository.refresh_mail_send_connection(encrypt_token_payload(tokens), int(connection["id"]), expires_at)
    except (EmailIntegrationError, KeyError, TypeError, ValueError):
        raise EmailOutboxError("Email send permission expired or could not be refreshed; no message was sent.") from None

    claimed = repository.claim_email_outbox(
        application_id,
        package_hash=package_hash,
        daily_limit=daily_limit,
        expected_scope_fingerprint=repository.automation_scope_fingerprint(),
        require_email_autopilot=autopilot,
    )
    if not claimed:
        raise EmailOutboxError("Application policy changed, the daily limit is reached, or this package was already attempted.")

    try:
        result = sender(
            str(package["provider"]),
            str(tokens["access_token"]),
            recipient=str(package["recipient"]),
            subject=str(package["subject"]),
            body=str(package["body"]),
            attachment_name=str(package["attachment_name"]),
            attachment_bytes=bytes(package["_attachment_bytes"]),
        )
    except ValueError:
        result = EmailSendResult("FAILED_FINAL", message="The reviewed email package was invalid; no automatic retry is allowed.")
    except Exception:
        result = EmailSendResult("UNKNOWN", message="The provider result was ambiguous; automatic retry is disabled.")
    repository.finish_email_outbox(
        application_id,
        state=result.state,
        message=result.message or "Provider accepted the request; delivery is not confirmed.",
        provider_reference=result.provider_reference,
    )
    return result
