from datetime import datetime
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import ORIGIN, PASSWORD, MakeClient, complete_setup
from fastapi.testclient import TestClient
from softauthn import SoftAuthenticator
from sqlalchemy import Connection, select

from myorb.auth.gate import AccessRequest, Decision, decide, safe_next
from myorb.auth.models import Passkey
from myorb.auth.passwords import hash_password, password_problem, verify_password
from myorb.auth.sessions import SESSION_COOKIE
from myorb.auth.throttle import LoginThrottle
from myorb.auth.tokens import sign, unsign
from myorb.db import Base

NEW_PASSWORD = "an even better password"

# Passwords and tokens


def test_password_hash_verifies_only_the_same_password():
    password_hash = hash_password(PASSWORD)
    assert password_hash.startswith("scrypt$")
    assert verify_password(PASSWORD, password_hash)
    assert not verify_password("wrong password", password_hash)


@pytest.mark.parametrize(
    "password_hash", ["", "plain", "md5$1$2$3$c2FsdA==$ZGln", "scrypt$x$8$1$$"]
)
def test_malformed_password_hash_never_verifies(password_hash: str):
    assert not verify_password(PASSWORD, password_hash)


@pytest.mark.parametrize(
    ("password", "confirmation", "ok"),
    [(PASSWORD, PASSWORD, True), ("short", "short", False), (PASSWORD, NEW_PASSWORD, False)],
)
def test_password_problem(password: str, confirmation: str, ok: bool):
    assert (password_problem(password, confirmation) is None) is ok


def test_token_round_trip():
    token = sign(b"key", "session", {"a": 1}, now=1000)
    assert unsign(b"key", token, "session", max_age=60, now=1030) == {"a": 1}


@pytest.mark.parametrize(
    ("key", "purpose", "now"),
    [(b"other key", "session", 1030), (b"key", "other", 1030), (b"key", "session", 1061)],
)
def test_token_rejected_with_wrong_key_purpose_or_age(key: bytes, purpose: str, now: float):
    token = sign(b"key", "session", {}, now=1000)
    assert unsign(key, token, purpose, max_age=60, now=now) is None


def test_tampered_token_is_rejected():
    body, signature = sign(b"key", "session", {"admin": False}, now=1000).split(".")
    forged = sign(b"attacker", "session", {"admin": True}, now=1000).split(".")[0]
    assert unsign(b"key", f"{forged}.{signature}", "session", max_age=60, now=1000) is None
    assert unsign(b"key", "garbage", "session", max_age=60, now=1000) is None


# Access decisions


def access(**overrides: Any) -> AccessRequest:
    defaults = {
        "path": "/",
        "method": "GET",
        "origin": None,
        "expected_origin": ORIGIN,
        "password_set": True,
        "authenticated": False,
    }
    return AccessRequest(**{**defaults, **overrides})


@pytest.mark.parametrize(
    ("request_", "decision"),
    [
        (access(path="/static/style.css", password_set=False), Decision.ALLOW),
        (access(path="/healthz", password_set=False), Decision.ALLOW),
        # Webhooks authenticate themselves with a token and come without an Origin header.
        (access(path="/hooks/health/x", method="POST"), Decision.ALLOW),
        (access(password_set=False), Decision.SETUP),
        (access(path="/setup", password_set=False), Decision.ALLOW),
        (access(path="/setup", method="POST", password_set=False), Decision.REJECT_ORIGIN),
        (access(path="/setup", method="POST", origin=ORIGIN, password_set=False), Decision.ALLOW),
        (access(), Decision.LOGIN),
        (access(authenticated=True), Decision.ALLOW),
        (access(path="/login"), Decision.ALLOW),
        (access(path="/login", method="POST", origin=ORIGIN), Decision.ALLOW),
        (
            access(path="/login", method="POST", origin="https://evil.example"),
            Decision.REJECT_ORIGIN,
        ),
        (access(path="/login", method="POST", origin="null"), Decision.REJECT_ORIGIN),
        (access(method="POST", authenticated=True), Decision.REJECT_ORIGIN),
        (access(method="POST", origin=ORIGIN, authenticated=True), Decision.ALLOW),
    ],
)
def test_access_decision(request_: AccessRequest, decision: Decision):
    assert decide(request_) is decision


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (None, "/"),
        ("/welcome/widgets?x=1", "/welcome/widgets?x=1"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
    ],
)
def test_safe_next(target: str | None, expected: str):
    assert safe_next(target) == expected


def test_throttle_blocks_after_max_failures_within_window():
    throttle = LoginThrottle(max_failures=2, window_seconds=60)
    throttle.record_failure("1.2.3.4", now=0)
    assert not throttle.is_blocked("1.2.3.4", now=1)
    throttle.record_failure("1.2.3.4", now=1)
    assert throttle.is_blocked("1.2.3.4", now=2)
    assert not throttle.is_blocked("5.6.7.8", now=2)
    assert not throttle.is_blocked("1.2.3.4", now=62)


# Setup


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_fresh_install_redirects_to_setup(make_client: MakeClient, method: str):
    response = make_client().request(method, "/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_setup_from_another_machine_requires_token(make_client: MakeClient):
    client = make_client()
    page = client.get("/setup")
    assert 'name="password"' not in page.text
    assert "docker logs" in page.text

    data = {"password": PASSWORD, "password_confirmation": PASSWORD, "token": "wrong"}
    assert client.post("/setup", data=data).status_code == 403
    assert not client.app.state.auth.password_set

    token = client.app.state.auth.setup_token
    assert 'name="password"' in client.get(f"/setup?token={token}").text


def test_setup_from_loopback_needs_no_token(make_client: MakeClient):
    client = make_client(client=("127.0.0.1", 50000))
    assert 'name="password"' in client.get("/setup").text
    data = {"password": PASSWORD, "password_confirmation": PASSWORD}
    response = client.post("/setup", data=data)
    assert response.url.path == "/"
    assert client.app.state.auth.password_set


def test_setup_rejects_weak_password(make_client: MakeClient):
    client = make_client()
    token = client.app.state.auth.setup_token
    data = {"password": "short", "password_confirmation": "short", "token": token}
    response = client.post("/setup", data=data)
    assert response.status_code == 400
    assert "at least 12 characters" in response.text


def test_setup_signs_in_and_cannot_run_again(client: TestClient):
    assert client.get("/").status_code == 200
    assert client.get("/setup").url.path == "/"


# Login and sessions


def test_anonymous_requests_are_sent_to_login(client: TestClient):
    client.cookies.clear()
    response = client.get("/welcome/widgets?x=1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/welcome/widgets%3Fx%3D1"

    response = client.get("/welcome/widgets", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"].startswith("/login")

    # A part of a page (a panel refreshing every minute) returns to the page it is on after
    # signing in, not to the bare part; a boosted link is a page of its own.
    part = {"HX-Request": "true", "HX-Current-URL": "http://testserver/tasks?edit=3"}
    response = client.get("/panels/calendar/next", headers=part)
    assert response.headers["HX-Redirect"] == "/login?next=/tasks%3Fedit%3D3"
    boosted = {"HX-Request": "true", "HX-Boosted": "true", "HX-Current-URL": "http://testserver/"}
    response = client.get("/tasks", headers=boosted)
    assert response.headers["HX-Redirect"] == "/login?next=/tasks"


def test_login_with_password(client: TestClient):
    client.cookies.clear()
    response = client.post("/login", data={"password": "wrong", "next": "/"})
    assert response.status_code == 401
    assert "Incorrect password" in response.text

    response = client.post("/login", data={"password": PASSWORD, "next": "//evil.example"})
    assert response.url.path == "/"
    assert 'id="orb-widgets"' in response.text


def test_login_is_throttled_after_repeated_failures(client: TestClient):
    client.cookies.clear()
    client.app.state.auth.throttle.max_failures = 3
    for _ in range(3):
        client.post("/login", data={"password": "wrong"})
    response = client.post("/login", data={"password": PASSWORD})
    assert response.status_code == 429


def test_logout(client: TestClient):
    response = client.post("/logout")
    assert response.url.path == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": ""}])
def test_cross_site_post_is_rejected(client: TestClient, headers: dict[str, str]):
    response = client.post("/logout", headers=headers)
    assert response.status_code == 403
    assert client.get("/").status_code == 200


def test_session_cookie_is_http_only(make_client: MakeClient):
    client = make_client()
    token = client.app.state.auth.setup_token
    data = {"password": PASSWORD, "password_confirmation": PASSWORD, "token": token}
    response = client.post("/setup", data=data, follow_redirects=False)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_password_survives_restart(make_client: MakeClient):
    complete_setup(make_client())
    restarted = make_client()
    assert restarted.app.state.auth.password_set
    assert restarted.post("/login", data={"password": PASSWORD}).url.path == "/"


def test_change_password_signs_out_other_devices(make_client: MakeClient):
    phone = make_client()
    complete_setup(phone)
    laptop = make_client()
    laptop.post("/login", data={"password": PASSWORD})
    # Both clients share the database but run separate apps; share the new key via the phone's app.
    laptop.app.state.auth = phone.app.state.auth

    data = {"current_password": "wrong", "password": NEW_PASSWORD}
    data["password_confirmation"] = NEW_PASSWORD
    assert phone.post("/settings/security/password", data=data).status_code == 400

    data["current_password"] = PASSWORD
    response = phone.post("/settings/security/password", data=data)
    assert "The password was changed" in response.text
    assert phone.get("/").status_code == 200
    assert laptop.get("/", follow_redirects=False).status_code == 303

    laptop.cookies.clear()
    assert laptop.post("/login", data={"password": PASSWORD}).status_code == 401
    assert laptop.post("/login", data={"password": NEW_PASSWORD}).url.path == "/"


def test_access_check_fails_closed(client: TestClient):
    del client.app.state.auth
    assert client.get("/").status_code == 503


# Passkeys


def register_passkey(client: TestClient, authenticator: SoftAuthenticator, name: str = "Laptop"):
    options = client.post("/settings/security/passkeys/options").json()
    assert options["authenticatorSelection"]["residentKey"] == "required"
    credential = authenticator.create(options)
    return client.post("/settings/security/passkeys", json={"name": name, "credential": credential})


def test_register_passkey_and_sign_in_with_it(client: TestClient):
    authenticator = SoftAuthenticator()
    assert register_passkey(client, authenticator).json() == {"ok": True}
    assert "Laptop" in client.get("/settings/security").text

    client.cookies.clear()
    login_page = client.get("/login")
    assert "data-passkey-login" in login_page.text
    options = client.post("/login/passkey/options").json()
    credential = authenticator.get(options)
    response = client.post(
        "/login/passkey/verify", json={"credential": credential, "next": "/settings/security"}
    )
    assert response.json() == {"redirect": "/settings/security"}
    assert client.get("/").status_code == 200

    passkey = passkey_rows(client)[0]
    assert passkey.sign_count == 1
    assert isinstance(passkey.last_used_at, datetime)


def test_passkey_from_another_origin_is_rejected(client: TestClient):
    response = register_passkey(client, SoftAuthenticator(origin="https://evil.example"))
    assert response.status_code == 400
    assert passkey_rows(client) == []


def test_passkey_sign_in_requires_matching_challenge(client: TestClient):
    authenticator = SoftAuthenticator()
    register_passkey(client, authenticator)
    client.cookies.clear()

    options = client.post("/login/passkey/options").json()
    options["challenge"] = "AAAA"
    response = client.post("/login/passkey/verify", json={"credential": authenticator.get(options)})
    assert response.status_code == 400

    client.cookies.clear()
    response = client.post("/login/passkey/verify", json={"credential": authenticator.get(options)})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_unknown_passkey_is_rejected(client: TestClient):
    client.cookies.clear()
    options = client.post("/login/passkey/options").json()
    response = client.post(
        "/login/passkey/verify", json={"credential": SoftAuthenticator().get(options)}
    )
    assert response.status_code == 400
    assert "not registered" in response.json()["detail"]


def test_remove_passkey(client: TestClient):
    register_passkey(client, SoftAuthenticator())
    passkey_id = passkey_rows(client)[0].id
    client.post(f"/settings/security/passkeys/{passkey_id}/delete")
    assert passkey_rows(client) == []


def test_registering_passkey_requires_sign_in(client: TestClient):
    client.cookies.clear()
    assert client.post("/settings/security/passkeys/options").status_code == 401


def passkey_rows(client: TestClient) -> list[Passkey]:
    async def query() -> list[Passkey]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(Passkey)))

    return client.portal.call(query)


# Database


def test_migrations_match_models(client: TestClient):
    def diff(connection: Connection) -> list:
        return compare_metadata(MigrationContext.configure(connection), Base.metadata)

    async def run() -> list:
        engine = client.app.state.sessionmaker.kw["bind"]
        async with engine.connect() as connection:
            return await connection.run_sync(diff)

    assert client.portal.call(run) == []
