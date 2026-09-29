"""Encrypted, single-application email drafts with candidate-confirmed recipients."""

from __future__ import annotations

from email.utils import parseaddr
from hashlib import sha256
import json
from pathlib import Path
import re
from collections.abc import Callable

from sampoagent.applications.runner import _active_verified_job
from sampoagent.integrations.email_oauth import (
    EmailIntegrationError,
    OAuthConfig,
    decrypt_token_payload,
    encrypt_token_payload,
    refresh_access_token,
    required_email_scope,
)
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
    if not application or application["status"] != "QUEUED" or application["queue_state"] != "READY":
        raise EmailOutboxError("Only a prepared, not-yet-submitted application can create an email draft.")
    job = repository.job(int(application["job_id"]))
    if not _active_verified_job(job or {}):
        raise EmailOutboxError("Recheck the current public job listing before creating an email application.")
    connection = repository.mail_send_connection()
    if not connection:
        raise EmailOutboxError("Connect a separate email-send account before preparing an email application.")
    cv_path, cv_bytes, cv_checksum = _application_cv(repository, storage_dir, application_id)
    candidate = repository.profile()
    if not candidate:
        raise EmailOutboxError("Create and review the candidate profile before preparing an email application.")
    subject, body = _email_copy(
        str(job.get("language", "en")),
        role=str(job.get("title", "")),
        company=str(job.get("company", "")),
        name=str(candidate.get("name", "")),
    )
    package = {
        "application_id": application_id,
        "provider": str(connection["provider"]),
        "send_connection_connected_at": str(connection["connected_at"]),
        "language": str(job.get("language", "en")),
        "role": str(job["title"]),
        "employer": str(job["company"]),
        "recipient": recipient,
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
            package_hash=package_hash,
            idempotency_key=sha256(f"application-email:{application_id}:{package_hash}".encode()).hexdigest(),
            payload_ciphertext=payload_ciphertext,
        )
    except EmailIntegrationError:
        raise EmailOutboxError("Email encryption is not configured; no email draft was stored.") from None
    except ValueError:
        raise EmailOutboxError("An email draft already exists for this application; review or cancel it first.") from None


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
    application = repository.application(application_id)
    if not application or application.get("status") != "QUEUED" or application.get("queue_state") != "EMAIL_READY":
        raise EmailOutboxError("The application is no longer eligible for an email send.")
    job = repository.job(int(application["job_id"]))
    if not job or not _active_verified_job(job) or not hmac_compare(str(package.get("job_fingerprint", "")), str(job.get("fingerprint", ""))):
        raise EmailOutboxError("The verified job listing changed or expired; review it again before sending.")
    if not hmac_compare(str(package.get("candidate_scope_fingerprint", "")), repository.automation_scope_fingerprint()):
        raise EmailOutboxError("Candidate facts, answers, or saved application scope changed; prepare and review the email again.")
    connection = repository.mail_send_connection()
    if (
        not connection or connection.get("provider") != package.get("provider")
        or connection.get("connected_at") != package.get("send_connection_connected_at")
    ):
        raise EmailOutboxError("The separate email-send account changed; review a newly prepared email draft.")
    scope = required_email_scope(str(connection["provider"]), "send")
    if scope not in str(connection.get("granted_scopes", "")).split():
        raise EmailOutboxError("The connected account has no separate send-only permission.")
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

    try:
        tokens = decrypt_token_payload(repository.mail_send_ciphertext() or "")
        if float(tokens.get("expires_at", 0)) <= __import__("time").time() + 60:
            refresh_token = str(tokens.get("refresh_token", ""))
            if not refresh_token:
                raise EmailIntegrationError("Reconnect email send permission.")
            config = OAuthConfig.from_environment(str(connection["provider"]))
            tokens = refresh_access_token(config, refresh_token=refresh_token, purpose="send")
            tokens["expires_at"] = __import__("time").time() + float(tokens.get("expires_in", 3600))
            repository.refresh_mail_send_connection(encrypt_token_payload(tokens))
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
