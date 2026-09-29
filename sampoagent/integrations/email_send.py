"""One-shot Gmail and Outlook send calls with conservative outcome tracking."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
import base64
import json
import re
from urllib.error import HTTPError
from urllib.request import Request

from sampoagent.integrations.email_oauth import _open_provider_request


@dataclass(frozen=True)
class EmailSendResult:
    state: str
    provider_reference: str = ""
    message: str = ""


# One-call Graph fileAttachment JSON has a 3 MB ceiling. Keep the raw PDF at
# 2 MiB so base64 and message-envelope overhead stay below that limit.
_MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


def _validate_email(
    provider: str,
    access_token: str,
    recipient: str,
    subject: str,
    body: str,
    attachment_name: str,
    attachment_bytes: bytes,
) -> None:
    if provider not in {"gmail", "microsoft"}:
        raise ValueError("Unsupported email provider")
    if not access_token or any(char in access_token for char in "\r\n"):
        raise ValueError("A valid provider access token is required")
    parsed = parseaddr(recipient)
    if (
        not recipient or parsed[1] != recipient or recipient.count("@") != 1
        or any(char in recipient for char in "\r\n,;<>") or any(char.isspace() for char in recipient)
    ):
        raise ValueError("One valid recipient email address is required")
    local, domain = recipient.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("One valid recipient email address is required")
    if not subject.strip() or len(subject) > 240 or any(char in subject for char in "\r\n\x00"):
        raise ValueError("Email subject is invalid")
    if not body.strip() or len(body) > 12_000 or "\x00" in body:
        raise ValueError("Email body is invalid")
    if (
        not attachment_name or len(attachment_name) > 120
        or attachment_name != attachment_name.replace("\\", "/").split("/")[-1]
        or not attachment_name.casefold().endswith(".pdf")
    ):
        raise ValueError("A plain PDF attachment filename is required")
    if not 5 <= len(attachment_bytes) <= _MAX_ATTACHMENT_BYTES or not attachment_bytes.startswith(b"%PDF-"):
        raise ValueError("Email attachment must be a PDF no larger than 2 MiB")


def _gmail_request(
    access_token: str,
    recipient: str,
    subject: str,
    body: str,
    attachment_name: str,
    attachment_bytes: bytes,
) -> Request:
    message = EmailMessage(policy=SMTP)
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(attachment_bytes, maintype="application", subtype="pdf", filename=attachment_name)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    payload = json.dumps({"raw": raw}, separators=(",", ":")).encode("utf-8")
    return Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )


def _graph_request(
    access_token: str,
    recipient: str,
    subject: str,
    body: str,
    attachment_name: str,
    attachment_bytes: bytes,
) -> Request:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": attachment_name,
                "contentType": "application/pdf",
                "contentBytes": base64.b64encode(attachment_bytes).decode("ascii"),
            }],
        },
        "saveToSentItems": True,
    }
    return Request(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )


def send_email_message(
    provider: str,
    access_token: str,
    *,
    recipient: str,
    subject: str,
    body: str,
    attachment_name: str,
    attachment_bytes: bytes,
    timeout_seconds: float = 15,
) -> EmailSendResult:
    """Make exactly one provider call; ambiguous transport outcomes are UNKNOWN.

    There is deliberately no internal retry. The caller must durably claim its
    outbox row before invoking this function and must never replay it.
    """
    _validate_email(provider, access_token, recipient, subject, body, attachment_name, attachment_bytes)
    request = (
        _gmail_request(access_token, recipient, subject, body, attachment_name, attachment_bytes)
        if provider == "gmail"
        else _graph_request(access_token, recipient, subject, body, attachment_name, attachment_bytes)
    )
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("Email provider timeout must be between 1 and 30 seconds")
    expected_status = 200 if provider == "gmail" else 202
    try:
        with _open_provider_request(request, timeout_seconds) as response:
            status = int(response.getcode())
            data = response.read(64_000)
    except HTTPError as exc:
        # Explicit client errors are provider rejections; 408/5xx can be
        # ambiguous because processing may have occurred before the response.
        if 400 <= exc.code < 500 and exc.code != 408:
            return EmailSendResult("FAILED_FINAL", message=f"Provider rejected the email request (HTTP {exc.code}); automatic retry is disabled.")
        return EmailSendResult("UNKNOWN", message="The email provider response was ambiguous; automatic retry is disabled.")
    except Exception:
        return EmailSendResult("UNKNOWN", message="The email provider response was ambiguous; automatic retry is disabled.")
    if status != expected_status:
        if 400 <= status < 500 and status != 408:
            return EmailSendResult("FAILED_FINAL", message=f"Provider rejected the email request (HTTP {status}); automatic retry is disabled.")
        return EmailSendResult("UNKNOWN", message="The email provider response was ambiguous; automatic retry is disabled.")
    reference = ""
    if provider == "gmail":
        try:
            response_payload = json.loads(data[:64_000])
            candidate_reference = str(response_payload.get("id", "")) if isinstance(response_payload, dict) else ""
            reference = re.sub(r"[^A-Za-z0-9._:-]", "", candidate_reference)[:160]
        except (ValueError, TypeError):
            return EmailSendResult("UNKNOWN", message="Gmail accepted a response that could not be verified; automatic retry is disabled.")
        if not reference:
            return EmailSendResult("UNKNOWN", message="Gmail accepted a response without a message reference; automatic retry is disabled.")
        return EmailSendResult("ACCEPTED", reference, "Gmail accepted the send request; delivery is not confirmed.")
    return EmailSendResult("ACCEPTED", message="Outlook accepted the send request (HTTP 202); delivery is not confirmed.")
