"""Read-only bounded mailbox synchronization for job-application replies."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sampoagent.integrations.email_oauth import EmailIntegrationError, OAuthConfig


@dataclass(frozen=True)
class MailboxMessage:
    provider_message_id: str
    sender: str
    subject: str
    snippet: str
    received_at: str
    link: str


_JOB_MAIL_MARKERS = re.compile(
    r"\b(application|applicant|interview|recruit(er|ment)|hiring|position|vacancy|job offer|thank you for applying)\b|haastattel|hakem|rekrytoin|työpaik|valint",
    re.IGNORECASE,
)


def is_likely_job_response(message: MailboxMessage) -> bool:
    return bool(_JOB_MAIL_MARKERS.search(f"{message.subject}\n{message.snippet}"))


def _json_request(url: str, *, access_token: str, timeout_seconds: float = 10) -> dict[str, object]:
    request = Request(url, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    class _SameHostRedirects(HTTPRedirectHandler):
        def redirect_request(self, original_request, fp, code, msg, headers, newurl):
            from urllib.parse import urlsplit

            start = urlsplit(original_request.full_url)
            destination = urlsplit(newurl)
            if start.scheme != "https" or destination.scheme != "https" or start.hostname != destination.hostname:
                raise EmailIntegrationError("Email provider attempted an unsafe redirect.")
            return super().redirect_request(original_request, fp, code, msg, headers, newurl)
    try:
        with build_opener(_SameHostRedirects()).open(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read(1_000_000))
    except Exception as exc:
        raise EmailIntegrationError("Could not read mailbox messages. Reconnect the account if this keeps happening.") from exc
    if not isinstance(data, dict):
        raise EmailIntegrationError("Email provider returned an invalid message list.")
    return data


def fetch_recent_messages(provider: str, access_token: str, *, limit: int = 25) -> list[MailboxMessage]:
    """Read metadata/snippets only; return likely job responses for review."""
    bound = max(1, min(int(limit), 50))
    if provider == "gmail":
        listing = _json_request("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode({"maxResults": bound, "q": "newer_than:90d (application OR interview OR job OR hiring OR recruiter OR hakemus OR haastattelu OR työpaikka OR rekrytointi)"}), access_token=access_token)
        refs = listing.get("messages")
        if not isinstance(refs, list):
            return []
        messages: list[MailboxMessage] = []
        for ref in refs[:bound]:
            if not isinstance(ref, dict) or not ref.get("id"):
                continue
            detail = _json_request(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{ref['id']}?" + urlencode([("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")]),
                access_token=access_token,
            )
            headers = detail.get("payload", {}).get("headers", []) if isinstance(detail.get("payload"), dict) else []
            values = {str(item.get("name", "")).casefold(): str(item.get("value", "")) for item in headers if isinstance(item, dict)} if isinstance(headers, list) else {}
            message = MailboxMessage(str(detail.get("id", ref["id"])), values.get("from", ""), values.get("subject", ""), str(detail.get("snippet", ""))[:300], values.get("date", ""), f"https://mail.google.com/mail/u/0/#all/{detail.get('id', ref['id'])}")
            if is_likely_job_response(message):
                messages.append(message)
        return messages
    if provider == "microsoft":
        url = "https://graph.microsoft.com/v1.0/me/messages?" + urlencode({"$top": bound, "$select": "id,subject,from,receivedDateTime,bodyPreview,webLink", "$orderby": "receivedDateTime desc"})
        data = _json_request(url, access_token=access_token)
        values = data.get("value")
        if not isinstance(values, list):
            return []
        results = []
        for item in values[:bound]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            sender_data = item.get("from", {}).get("emailAddress", {}) if isinstance(item.get("from"), dict) else {}
            message = MailboxMessage(
                str(item["id"]), str(sender_data.get("address", "")), str(item.get("subject", "")),
                str(item.get("bodyPreview", ""))[:300], str(item.get("receivedDateTime", "")), str(item.get("webLink", "")),
            )
            if is_likely_job_response(message):
                results.append(message)
        return results
    raise EmailIntegrationError("Unsupported email provider.")
