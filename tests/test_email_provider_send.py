from email import message_from_bytes
import json

import pytest

from sampoagent.integrations.email_send import send_email_message


class _Response:
    def __init__(self, status: int, payload: bytes = b"") -> None:
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _limit):
        return self.payload


def test_gmail_send_is_single_provider_request_with_exact_cv_attachment(monkeypatch):
    import sampoagent.integrations.email_send as sender

    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return _Response(200, b'{"id":"gmail-message-1"}')

    monkeypatch.setattr(sender, "_open_provider_request", open_request)
    result = send_email_message(
        "gmail", "access-token", recipient="recruitment@northstar.example",
        subject="Application: Cleaner", body="CV attached.",
        attachment_name="aino_cv.pdf", attachment_bytes=b"%PDF-1.4\nsynthetic\n%%EOF",
    )

    assert result.state == "ACCEPTED"
    assert result.provider_reference == "gmail-message-1"
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer access-token"
    assert timeout <= 20
    encoded = json.loads(request.data)["raw"]
    import base64
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    message = message_from_bytes(raw)
    assert message["To"] == "recruitment@northstar.example"
    parts = list(message.walk())
    attachment = next(part for part in parts if part.get_filename() == "aino_cv.pdf")
    assert attachment.get_payload(decode=True) == b"%PDF-1.4\nsynthetic\n%%EOF"


def test_graph_202_is_provider_accepted_not_delivered(monkeypatch):
    import sampoagent.integrations.email_send as sender

    requests = []
    monkeypatch.setattr(sender, "_open_provider_request", lambda request, timeout: (requests.append(request) or _Response(202)))
    result = send_email_message(
        "microsoft", "access-token", recipient="recruitment@northstar.example",
        subject="Application", body="CV attached.", attachment_name="aino_cv.pdf",
        attachment_bytes=b"%PDF-1.4\nsynthetic\n%%EOF",
    )

    assert result.state == "ACCEPTED"
    assert result.provider_reference == ""
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://graph.microsoft.com/v1.0/me/sendMail"
    body = json.loads(request.data)
    assert body["message"]["toRecipients"][0]["emailAddress"]["address"] == "recruitment@northstar.example"
    assert body["message"]["attachments"][0]["contentType"] == "application/pdf"
    assert body["message"]["attachments"][0]["name"] == "aino_cv.pdf"


def test_timeout_is_unknown_and_must_not_be_retried(monkeypatch):
    import sampoagent.integrations.email_send as sender

    attempts = []

    def timeout(_request, _timeout):
        attempts.append("attempt")
        raise TimeoutError("synthetic timeout after request")

    monkeypatch.setattr(sender, "_open_provider_request", timeout)
    result = send_email_message(
        "gmail", "access-token", recipient="recruitment@northstar.example",
        subject="Application", body="CV attached.", attachment_name="aino_cv.pdf",
        attachment_bytes=b"%PDF-1.4\nsynthetic\n%%EOF",
    )

    assert result.state == "UNKNOWN"
    assert len(attempts) == 1


@pytest.mark.parametrize("provider", ["gmail", "microsoft"])
def test_invalid_recipient_or_attachment_is_rejected_before_network(provider, monkeypatch):
    import sampoagent.integrations.email_send as sender

    calls = []
    monkeypatch.setattr(sender, "_open_provider_request", lambda *_args: calls.append("request"))
    with pytest.raises(ValueError):
        send_email_message(
            provider, "access-token", recipient="victim@example.test\r\nBcc: attacker@example.test",
            subject="Application", body="CV attached.", attachment_name="aino_cv.pdf",
            attachment_bytes=b"not a PDF",
        )
    assert not calls


def test_attachment_over_direct_send_limit_is_rejected_before_network(monkeypatch):
    import sampoagent.integrations.email_send as sender

    calls = []
    monkeypatch.setattr(sender, "_open_provider_request", lambda *_args: calls.append("request"))
    with pytest.raises(ValueError):
        send_email_message(
            "microsoft", "access-token", recipient="recruitment@northstar.example",
            subject="Application", body="CV attached.", attachment_name="aino_cv.pdf",
            attachment_bytes=b"%PDF-" + b"0" * (2 * 1024 * 1024),
        )
    assert not calls
