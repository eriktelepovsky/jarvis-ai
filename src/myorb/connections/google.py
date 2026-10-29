"""Google OAuth 2.0 for self-hosted installations, using the user's own OAuth client."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx2

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

PROVIDER = "google"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
BASE_SCOPES = ("openid", EMAIL_SCOPE)


@dataclass(frozen=True)
class Capability:
    """Something myOrb can do with a Google account, and how to check that it works."""

    id: str
    label: str
    scope: str
    check_url: str
    api_name: str
    api_url: str


CAPABILITIES = (
    Capability(
        id="gmail.read",
        label="Read Gmail",
        scope="https://www.googleapis.com/auth/gmail.readonly",
        check_url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
        api_name="Gmail API",
        api_url="https://console.cloud.google.com/apis/library/gmail.googleapis.com",
    ),
    Capability(
        id="gmail.modify",
        label="Mark Gmail as read",
        scope="https://www.googleapis.com/auth/gmail.modify",
        check_url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
        api_name="Gmail API",
        api_url="https://console.cloud.google.com/apis/library/gmail.googleapis.com",
    ),
    Capability(
        id="calendar.read",
        label="Read Google Calendar",
        scope="https://www.googleapis.com/auth/calendar.readonly",
        check_url="https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1",
        api_name="Google Calendar API",
        api_url="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
    ),
    Capability(
        id="calendar.events",
        label="Add and change calendar events",
        scope="https://www.googleapis.com/auth/calendar.events",
        check_url="https://www.googleapis.com/calendar/v3/calendars/primary/events?maxResults=1",
        api_name="Google Calendar API",
        api_url="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
    ),
    Capability(
        id="youtube.read",
        label="Read YouTube subscriptions",
        scope="https://www.googleapis.com/auth/youtube.readonly",
        check_url="https://www.googleapis.com/youtube/v3/subscriptions?mine=true&part=id&maxResults=1",
        api_name="YouTube Data API v3",
        api_url="https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    ),
)


class GoogleError(Exception):
    """A Google request failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str


def is_valid_client_id(client_id: str) -> bool:
    return client_id.endswith(".apps.googleusercontent.com") and " " not in client_id


def requested_scopes() -> list[str]:
    return [*BASE_SCOPES, *(capability.scope for capability in CAPABILITIES)]


def pkce_pair() -> tuple[str, str]:
    """Return a PKCE code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    client: OAuthClient, redirect_uri: str, state: str, code_challenge: str
) -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(requested_scopes()),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # A refresh token lets myOrb work in the background; Google only issues one together
        # with a consent prompt.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _token_request(http: httpx2.AsyncClient, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = await http.post(TOKEN_URL, data=data)
    except httpx2.HTTPError as error:
        raise GoogleError("Google could not be reached. Try again later.") from error
    body = response.json() if response.content else {}
    if response.status_code == 400 and body.get("error") == "invalid_grant":
        raise GoogleError("Google no longer accepts this connection. Connect the account again.")
    if response.status_code in (400, 401) and body.get("error") == "invalid_client":
        raise GoogleError("Google rejected the OAuth client. Check the client ID and secret.")
    if response.status_code != 200:
        raise GoogleError(f"Google returned an error (HTTP {response.status_code}).")
    return body


async def exchange_code(
    http: httpx2.AsyncClient, client: OAuthClient, code: str, redirect_uri: str, verifier: str
) -> dict[str, Any]:
    return await _token_request(
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )


async def refresh_access_token(
    http: httpx2.AsyncClient, client: OAuthClient, refresh_token: str
) -> tuple[str, int]:
    """Return a new access token and its lifetime in seconds."""
    body = await _token_request(
        http,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )
    return body["access_token"], int(body.get("expires_in", 3600))


async def fetch_email(http: httpx2.AsyncClient, access_token: str) -> str:
    try:
        response = await http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    except httpx2.HTTPError as error:
        raise GoogleError("Google could not be reached. Try again later.") from error
    if response.status_code != 200 or not response.json().get("email"):
        raise GoogleError("Google did not share the account's email address.")
    return response.json()["email"]


def is_api_disabled(response: httpx2.Response) -> bool:
    reason = _error_reason(response)
    return response.status_code == 403 and (
        "SERVICE_DISABLED" in reason or "accessNotConfigured" in reason
    )


def is_rate_limited(response: httpx2.Response) -> bool:
    """Too many requests: 429, or 403 with a rate-limit reason (as Gmail and Calendar answer),
    not a missing permission."""
    if response.status_code == 429:
        return True
    reason = _error_reason(response)
    return response.status_code == 403 and any(
        word in reason for word in ("rateLimitExceeded", "RateLimitExceeded", "RATE_LIMIT_EXCEEDED")
    )


def _error_reason(response: httpx2.Response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return ""
    reasons = [detail.get("reason", "") for detail in error.get("details", [])]
    reasons += [item.get("reason", "") for item in error.get("errors", [])]
    return " ".join(reasons)


async def check_capabilities(
    http: httpx2.AsyncClient, access_token: str, granted_scopes: set[str]
) -> list[dict[str, Any]]:
    """Check each capability with a cheap read request and explain what to fix if it fails."""
    results = []
    for capability in CAPABILITIES:
        result: dict[str, Any] = {"id": capability.id, "label": capability.label, "ok": False}
        if capability.scope not in granted_scopes:
            result["detail"] = "Permission was not granted. Connect the account again and allow it."
            results.append(result)
            continue
        try:
            response = await http.get(
                capability.check_url, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx2.HTTPError:
            result["detail"] = "Google could not be reached."
            results.append(result)
            continue
        if response.status_code == 200:
            result["ok"] = True
        elif is_api_disabled(response):
            result["detail"] = f"The {capability.api_name} is not enabled in your Google project."
            result["fix_url"] = capability.api_url
        else:
            result["detail"] = f"Google returned an error (HTTP {response.status_code})."
        results.append(result)
    return results


async def revoke(http: httpx2.AsyncClient, token: str) -> None:
    """Revoke a token at Google. Best effort: the connection is removed locally either way."""
    try:
        await http.post(REVOKE_URL, data={"token": token})
    except httpx2.HTTPError:
        pass
