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
