"""Read-only Gmail and Microsoft mailbox OAuth helpers."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.fernet import Fernet, InvalidToken


class EmailIntegrationError(ValueError):
    """Safe, user-facing mailbox configuration or request error."""


class _ProviderRedirects(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> Request | None:
        original = urlsplit(request.full_url)
        redirected = urlsplit(newurl)
        if original.scheme != "https" or redirected.scheme != "https" or original.hostname != redirected.hostname:
            raise EmailIntegrationError("Email provider attempted an unsafe redirect.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open_provider_request(request: Request, timeout_seconds: float):
    return build_opener(_ProviderRedirects()).open(request, timeout=timeout_seconds)


@dataclass(frozen=True)
class OAuthConfig:
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_environment(cls, provider: str) -> "OAuthConfig":
        if provider == "gmail":
            client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
            client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
            redirect_uri = os.environ.get("SAMPOAGENT_GOOGLE_REDIRECT_URI", "http://127.0.0.1:8765/email/callback/gmail").strip()
        elif provider == "microsoft":
            client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
            client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
            redirect_uri = os.environ.get("SAMPOAGENT_MICROSOFT_REDIRECT_URI", "http://127.0.0.1:8765/email/callback/microsoft").strip()
        else:
            raise EmailIntegrationError("Unsupported email provider.")
        if not client_id or not client_secret:
            raise EmailIntegrationError(f"{provider.title()} OAuth is not configured yet.")
        parsed_redirect = urlsplit(redirect_uri)
        expected_path = f"/email/callback/{provider}"
        if parsed_redirect.scheme != "http" or parsed_redirect.hostname not in {"127.0.0.1", "localhost"} or not parsed_redirect.port or parsed_redirect.path != expected_path or parsed_redirect.username or parsed_redirect.password or parsed_redirect.query or parsed_redirect.fragment:
            raise EmailIntegrationError("Email OAuth callback must point to this local SampoAgent instance.")
        return cls(provider, client_id, client_secret, redirect_uri)


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def authorization_url(config: OAuthConfig, *, state: str, challenge: str) -> str:
    if config.provider == "gmail":
        endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
        scope = "https://www.googleapis.com/auth/gmail.readonly"
        params = {
            "client_id": config.client_id, "redirect_uri": config.redirect_uri,
            "response_type": "code", "scope": scope, "state": state,
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        }
    else:
        endpoint = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        params = {
            "client_id": config.client_id, "redirect_uri": config.redirect_uri,
            "response_type": "code", "response_mode": "query",
            "scope": "offline_access User.Read Mail.Read", "state": state,
        }
    params.update({"code_challenge": challenge, "code_challenge_method": "S256"})
    return endpoint + "?" + urlencode(params)


def _token_endpoint(provider: str) -> str:
    if provider == "gmail":
        return "https://oauth2.googleapis.com/token"
    if provider == "microsoft":
        return "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    raise EmailIntegrationError("Unsupported email provider.")


def exchange_code(config: OAuthConfig, *, code: str, verifier: str, timeout_seconds: float = 10) -> dict[str, object]:
    payload = urlencode({
        "client_id": config.client_id, "client_secret": config.client_secret,
        "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
        "redirect_uri": config.redirect_uri,
    }).encode("ascii")
    request = Request(_token_endpoint(config.provider), data=payload, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with _open_provider_request(request, timeout_seconds) as response:
            data = json.loads(response.read(64_000))
    except Exception as exc:
        # Provider errors may contain request metadata; deliberately omit them.
        raise EmailIntegrationError("Email authorization could not be completed. Check provider setup and try again.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise EmailIntegrationError("Email provider did not return an access token.")
    return data


def refresh_access_token(config: OAuthConfig, *, refresh_token: str, timeout_seconds: float = 10) -> dict[str, object]:
    from urllib.parse import urlencode

    payload = urlencode({
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        **({"scope": "https://www.googleapis.com/auth/gmail.readonly"} if config.provider == "gmail" else {"scope": "offline_access User.Read Mail.Read"}),
    }).encode("ascii")
    request = Request(_token_endpoint(config.provider), data=payload, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with _open_provider_request(request, timeout_seconds) as response:
            data = json.loads(response.read(64_000))
    except Exception as exc:
        raise EmailIntegrationError("Saved email authorization expired. Reconnect the mailbox and try again.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise EmailIntegrationError("Email provider could not refresh authorization. Reconnect the mailbox.")
    if not data.get("refresh_token"):
        data["refresh_token"] = refresh_token
    return data


def encrypt_token_payload(payload: dict[str, object], key: str | None = None) -> str:
    raw_key = (key or os.environ.get("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", "")).strip()
    if not raw_key:
        raise EmailIntegrationError("Set SAMPOAGENT_TOKEN_ENCRYPTION_KEY before connecting email.")
    try:
        fernet = Fernet(raw_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise EmailIntegrationError("SAMPOAGENT_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc
    return fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decrypt_token_payload(ciphertext: str, key: str | None = None) -> dict[str, object]:
    raw_key = (key or os.environ.get("SAMPOAGENT_TOKEN_ENCRYPTION_KEY", "")).strip()
    if not raw_key:
        raise EmailIntegrationError("Email encryption key is not configured.")
    try:
        raw = Fernet(raw_key.encode("ascii")).decrypt(ciphertext.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, UnicodeEncodeError, InvalidToken, json.JSONDecodeError) as exc:
        raise EmailIntegrationError("Saved email connection cannot be decrypted. Disconnect and reconnect it.") from exc
    if not isinstance(data, dict):
        raise EmailIntegrationError("Saved email connection is invalid.")
    return data
