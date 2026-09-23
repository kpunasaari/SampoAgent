import pytest

from sampoagent.integrations.email_oauth import EmailIntegrationError
from sampoagent.integrations.mailbox import MailboxMessage, fetch_recent_messages, is_likely_job_response


def test_job_response_classifier_uses_only_bounded_metadata():
    assert is_likely_job_response(MailboxMessage("1", "recruiter@example.org", "Interview invitation", "Can we meet Tuesday?", "today", "https://mail.google.com/"))
    assert not is_likely_job_response(MailboxMessage("2", "shop@example.org", "Your order shipped", "Delivery tomorrow", "today", "https://mail.google.com/"))
    assert is_likely_job_response(MailboxMessage("3", "hr@example.fi", "Kiitos hakemuksestasi", "Kutsumme sinut haastatteluun.", "today", "https://mail.google.com/"))
    assert is_likely_job_response(MailboxMessage("4", "hr@example.fi", "Kutsu työhaastatteluun", "", "today", "https://mail.google.com/"))
    assert is_likely_job_response(MailboxMessage("5", "hr@example.fi", "Työhakemuksesi on vastaanotettu", "", "today", "https://mail.google.com/"))
    assert is_likely_job_response(MailboxMessage("6", "hr@example.fi", "Hakemus vastaanotettu", "", "today", "https://mail.google.com/"))


def test_gmail_sync_uses_readonly_metadata_and_limits_message_count(monkeypatch):
    calls = []

    def fake_request(url, *, access_token, timeout_seconds=10):
        calls.append(url)
        if "users/me/messages?" in url:
            return {"messages": [{"id": "msg-1"}, {"id": "msg-2"}]}
        if "/msg-2?" in url:
            return {"id": "msg-2", "snippet": "Your order shipped.", "payload": {"headers": [{"name": "From", "value": "Shop <shop@example.org>"}, {"name": "Subject", "value": "Order confirmation"}]}}
        return {"id": "msg-1", "snippet": "Please schedule an interview.", "payload": {"headers": [{"name": "From", "value": "HR <hr@example.org>"}, {"name": "Subject", "value": "Interview invitation"}, {"name": "Date", "value": "today"}]}}

    monkeypatch.setattr("sampoagent.integrations.mailbox._json_request", fake_request)
    messages = fetch_recent_messages("gmail", "secret-token", limit=500)

    assert len(messages) == 1
    assert messages[0].sender == "HR <hr@example.org>"
    assert len(calls) == 3
    assert "maxResults=50" in calls[0]
    assert "hakemus" in calls[0]
    assert "format=metadata" in calls[1]


def test_microsoft_sync_rejects_unknown_provider_and_caps_result(monkeypatch):
    with pytest.raises(EmailIntegrationError, match="Unsupported"):
        fetch_recent_messages("other", "token")

    monkeypatch.setattr("sampoagent.integrations.mailbox._json_request", lambda *args, **kwargs: {"value": []})
    assert fetch_recent_messages("microsoft", "token", limit=-1) == []
