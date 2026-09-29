from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
import pytest

from sampoagent.integrations.email_oauth import (
    EmailIntegrationError,
    OAuthConfig,
    authorization_url,
    create_pkce_pair,
    decrypt_token_payload,
    encrypt_token_payload,
    fetch_send_account_identity,
)


@pytest.mark.parametrize("provider,scope", [
    ("gmail", "https://www.googleapis.com/auth/gmail.readonly"),
    ("microsoft", "offline_access User.Read Mail.Read"),
])
def test_oauth_authorization_uses_read_only_scopes_and_pkce(provider, scope):
    config = OAuthConfig(provider, "client-id", "client-secret", f"http://127.0.0.1:8765/email/callback/{provider}")
    verifier, challenge = create_pkce_pair()
    params = parse_qs(urlsplit(authorization_url(config, state="random-state", challenge=challenge)).query)

    assert params["state"] == ["random-state"]
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == [scope]
    assert verifier not in challenge


@pytest.mark.parametrize("provider,scope", [
    ("gmail", "openid email https://www.googleapis.com/auth/gmail.send"),
    ("microsoft", "openid email offline_access Mail.Send"),
])
def test_outgoing_email_uses_separate_send_scope_without_read_permission(provider, scope):
    config = OAuthConfig(provider, "client-id", "client-secret", f"http://127.0.0.1:8765/email/callback/{provider}")
    _, challenge = create_pkce_pair()
    params = parse_qs(urlsplit(authorization_url(config, state="send-state", challenge=challenge, purpose="send")).query)

    assert params["scope"] == [scope]
    assert "Mail.Read" not in params["scope"][0]
    assert "gmail.readonly" not in params["scope"][0]
    assert "User.Read" not in params["scope"][0]


@pytest.mark.parametrize("provider,claims", [
    ("gmail", {"sub": "google-subject", "email": "sender@gmail.test", "email_verified": True}),
    ("microsoft", {"sub": "microsoft-subject", "email": "sender@outlook.test"}),
])
def test_send_account_identity_uses_fixed_provider_userinfo(provider, claims, monkeypatch):
    import sampoagent.integrations.email_oauth as oauth

    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            import json
            return json.dumps(claims).encode()

    monkeypatch.setattr(oauth, "_open_provider_request", lambda request, timeout: (requests.append(request) or Response()))
    identity = fetch_send_account_identity(provider, "synthetic-access-token")

    assert identity["subject"] == claims["sub"]
    assert identity["sender_email"] == claims["email"]
    assert identity["address_status"] == ("verified" if provider == "gmail" else "provider_reported")
    assert requests[0].full_url == (
        "https://openidconnect.googleapis.com/v1/userinfo"
        if provider == "gmail" else "https://graph.microsoft.com/oidc/userinfo"
    )
    assert requests[0].get_header("Authorization") == "Bearer synthetic-access-token"


@pytest.mark.parametrize("provider,claims", [
    ("gmail", {"sub": "google-subject", "email": "sender@gmail.test", "email_verified": False}),
    ("gmail", {"sub": "google-subject", "email": "bad address", "email_verified": True}),
    ("microsoft", {"email": "sender@outlook.test"}),
    ("microsoft", {"sub": "microsoft-subject", "email": "not-an-email"}),
])
def test_invalid_send_account_identity_fails_closed(provider, claims, monkeypatch):
    import json
    import sampoagent.integrations.email_oauth as oauth

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(claims).encode()

    monkeypatch.setattr(oauth, "_open_provider_request", lambda *_args: Response())
    with pytest.raises(EmailIntegrationError):
        fetch_send_account_identity(provider, "synthetic-access-token")


@pytest.mark.parametrize("provider,expected_scope", [
    ("gmail", "https://www.googleapis.com/auth/gmail.send"),
    ("microsoft", "offline_access Mail.Send"),
])
def test_send_token_refresh_keeps_only_the_original_send_capability(provider, expected_scope, monkeypatch):
    import json
    import sampoagent.integrations.email_oauth as oauth

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"access_token": "refreshed"}).encode()

    captured = []
    monkeypatch.setattr(oauth, "_open_provider_request", lambda request, _timeout: (captured.append(request) or Response()))
    config = OAuthConfig(provider, "client", "secret", f"http://127.0.0.1:8765/email/callback/{provider}")

    assert oauth.refresh_access_token(config, refresh_token="refresh", purpose="send")["access_token"] == "refreshed"
    form = parse_qs(captured[0].data.decode())
    assert form["scope"] == [expected_scope]
    assert "openid" not in form["scope"][0]
    assert "email" not in form["scope"][0]


def test_oauth_config_requires_provider_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(EmailIntegrationError, match="not configured"):
        OAuthConfig.from_environment("gmail")


def test_oauth_config_rejects_nonlocal_callback(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "local-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "local-client-secret")
    monkeypatch.setenv("SAMPOAGENT_GOOGLE_REDIRECT_URI", "https://attacker.example/email/callback/gmail")
    with pytest.raises(EmailIntegrationError, match="local SampoAgent"):
        OAuthConfig.from_environment("gmail")


def test_token_payload_is_encrypted_and_requires_the_same_key():
    key = Fernet.generate_key().decode()
    cipher = encrypt_token_payload({"access_token": "private", "refresh_token": "secret"}, key)
    assert "private" not in cipher
    assert decrypt_token_payload(cipher, key)["refresh_token"] == "secret"
    with pytest.raises(EmailIntegrationError, match="cannot be decrypted"):
        decrypt_token_payload(cipher, Fernet.generate_key().decode())


def test_invalid_encryption_key_is_rejected():
    with pytest.raises(EmailIntegrationError, match="valid Fernet key"):
        encrypt_token_payload({"access_token": "x"}, "not-a-fernet-key")
