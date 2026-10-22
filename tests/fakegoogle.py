"""A fake of Google's OAuth and API endpoints for tests, used as an httpx2 transport."""

import base64
import hashlib
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx2

from myorb.connections import google

CLIENT_ID = "123-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secret"
REDIRECT_URI = "http://testserver/integrations/google/callback"
EMAIL = "tony@stark.example"
ALL_SCOPES = " ".join(google.requested_scopes())


class FakeGoogle:
    """Google's OAuth and API endpoints, as an httpx2 transport."""

    def __init__(self) -> None:
        self.challenges: dict[str, str] = {}
        self.refresh_tokens = {"refresh-1"}
        self.granted_scopes = ALL_SCOPES
        self.disabled_apis: set[str] = set()
        self.revoked: list[str] = []
        self.refreshes = 0
        # Extra API endpoints by URL prefix, e.g. Gmail and Calendar.
        self.routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}

    def authorize(self, url: str) -> tuple[str, str]:
        """Play the user approving access; return the state and code for the callback."""
        params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        code = f"code-{len(self.challenges)}"
        self.challenges[code] = params["code_challenge"]
        return params["state"], code

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == google.TOKEN_URL:
            return self._token(dict(parse_qs(request.content.decode())))
        if url == google.REVOKE_URL:
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx2.Response(200)
        if request.headers.get("Authorization") != "Bearer access-1":
            return httpx2.Response(401)
        if url == google.USERINFO_URL:
            return httpx2.Response(200, json={"email": EMAIL})
        for capability in google.CAPABILITIES:
            if url == capability.check_url:
                if capability.api_name in self.disabled_apis:
                    details = [{"reason": "SERVICE_DISABLED"}]
                    return httpx2.Response(403, json={"error": {"details": details}})
                return httpx2.Response(200, json={})
        for prefix, handler in self.routes.items():
            if url.startswith(prefix):
                return handler(request)
        return httpx2.Response(404)

    def _token(self, form: dict[str, list[str]]) -> httpx2.Response:
        data = {key: values[0] for key, values in form.items()}
        if (data["client_id"], data["client_secret"]) != (CLIENT_ID, CLIENT_SECRET):
            return httpx2.Response(401, json={"error": "invalid_client"})
        if data["grant_type"] == "authorization_code":
            challenge = self.challenges.pop(data["code"], None)
            digest = hashlib.sha256(data["code_verifier"].encode()).digest()
            expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if challenge != expected or data["redirect_uri"] != REDIRECT_URI:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            body = {"access_token": "access-1", "refresh_token": "refresh-1"}
            return httpx2.Response(200, json={**body, "scope": self.granted_scopes})
        self.refreshes += 1
        if data["refresh_token"] not in self.refresh_tokens:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        return httpx2.Response(200, json={"access_token": "access-1"})
