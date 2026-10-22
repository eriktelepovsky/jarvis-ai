import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fakegoogle import CLIENT_ID, CLIENT_SECRET, EMAIL, REDIRECT_URI, FakeGoogle
from fastapi.testclient import TestClient
from sqlalchemy import select

from jarvis_hud.connections import google
from jarvis_hud.connections.models import Connection, ProviderSettings
from jarvis_hud.crypto import InvalidToken, SecretBox, load_secret_key


@pytest.fixture
def fake_google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def client(make_client: MakeClient, fake_google: FakeGoogle) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(fake_google))
    complete_setup(client)
    return client


def save_oauth_client(client: TestClient) -> None:
    data = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    response = client.post("/integrations/google/setup", data=data)
    assert "The OAuth client was saved" in response.text


def connect(client: TestClient, fake_google: FakeGoogle) -> httpx2.Response:
    response = client.post("/integrations/google/connect", follow_redirects=False)
    assert response.status_code == 303
    state, code = fake_google.authorize(response.headers["location"])
    return client.get("/integrations/google/callback", params={"state": state, "code": code})


def rows(client: TestClient, model: type) -> list:
    async def query() -> list:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(model)))

    return client.portal.call(query)


# Setup


def test_integrations_require_sign_in(client: TestClient):
    client.cookies.clear()
    assert client.get("/integrations", follow_redirects=False).status_code == 303


def test_setup_wizard_shows_redirect_uri_and_validates_client(client: TestClient):
    assert "Set up Google" in client.get("/integrations").text
    page = client.get("/integrations/google/setup")
    assert REDIRECT_URI in page.text

    data = {"client_id": "not-a-client-id", "client_secret": CLIENT_SECRET}
    response = client.post("/integrations/google/setup", data=data)
    assert response.status_code == 400
    assert "apps.googleusercontent.com" in response.text


def test_saved_client_secret_is_encrypted_and_kept(client: TestClient):
    save_oauth_client(client)
    assert CLIENT_SECRET.encode() not in rows(client, ProviderSettings)[0].data

    # Saving again with an empty secret keeps the stored one.
    response = client.post("/integrations/google/setup", data={"client_id": CLIENT_ID})
    assert "The OAuth client was saved" in response.text


def test_connect_without_client_goes_to_setup(client: TestClient):
    response = client.post("/integrations/google/connect", follow_redirects=False)
    assert response.headers["location"] == "/integrations/google/setup"


# Connecting


def test_authorization_request(client: TestClient):
    save_oauth_client(client)
    response = client.post("/integrations/google/connect", follow_redirects=False)
    url = urlsplit(response.headers["location"])
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == google.AUTHORIZE_URL
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["code_challenge_method"] == "S256"
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert set(params["scope"].split()) == set(google.requested_scopes())

    cookie = response.headers["set-cookie"]
    assert "Path=/integrations/google/callback" in cookie
    assert "HttpOnly" in cookie


def test_connect_account(client: TestClient, fake_google: FakeGoogle):
    save_oauth_client(client)
    response = connect(client, fake_google)
    assert "The Google account was connected" in response.text
    assert EMAIL in response.text
    assert response.text.count("Works") == 3

    [connection] = rows(client, Connection)
    assert b"refresh-1" not in connection.credentials
    assert client.app.state.secret_box.decrypt(connection.credentials) == {
        "refresh_token": "refresh-1"
    }


def test_connecting_same_account_again_updates_it(client: TestClient, fake_google: FakeGoogle):
    save_oauth_client(client)
    connect(client, fake_google)
    connect(client, fake_google)
    assert len(rows(client, Connection)) == 1


@pytest.mark.parametrize("cookie", [True, False])
def test_callback_rejects_unknown_state(client: TestClient, fake_google: FakeGoogle, cookie: bool):
    save_oauth_client(client)
    response = client.post("/integrations/google/connect", follow_redirects=False)
    state, code = fake_google.authorize(response.headers["location"])
    if cookie:
        state = "forged"
    else:
        # The right state alone is not enough: it must match the cookie of this browser.
        client.cookies.delete("jarvis_google_oauth")
    response = client.get("/integrations/google/callback", params={"state": state, "code": code})
    assert response.status_code == 400
    assert "expired or did not start here" in response.text
    assert rows(client, Connection) == []


def test_denied_access_is_not_echoed(client: TestClient, fake_google: FakeGoogle):
    save_oauth_client(client)
    response = client.post("/integrations/google/connect", follow_redirects=False)
    state, _ = fake_google.authorize(response.headers["location"])
    response = client.get(
        "/integrations/google/callback", params={"state": state, "error": "<script>x</script>"}
    )
    assert "Access was not granted" in response.text
    assert "<script>x" not in response.text


def test_disabled_api_is_reported_with_link(client: TestClient, fake_google: FakeGoogle):
    fake_google.disabled_apis.add("Gmail API")
    save_oauth_client(client)
    response = connect(client, fake_google)
    assert "The Gmail API is not enabled in your Google project" in response.text
    assert "https://console.cloud.google.com/apis/library/gmail.googleapis.com" in response.text
    assert response.text.count("Works") == 2


def test_missing_permission_is_reported(client: TestClient, fake_google: FakeGoogle):
    fake_google.granted_scopes = " ".join(google.BASE_SCOPES)
    save_oauth_client(client)
    response = connect(client, fake_google)
    assert response.text.count("Permission was not granted") == 2


def test_revoked_access_is_reported_on_test(client: TestClient, fake_google: FakeGoogle):
    save_oauth_client(client)
    connect(client, fake_google)
    fake_google.refresh_tokens.clear()
    connection_id = rows(client, Connection)[0].id
    response = client.post(f"/integrations/google/{connection_id}/test")
    assert "Connect the account again" in response.text
    assert "Works" not in response.text


def test_disconnect_revokes_and_removes(client: TestClient, fake_google: FakeGoogle):
    save_oauth_client(client)
    connect(client, fake_google)
    connection_id = rows(client, Connection)[0].id
    response = client.post(f"/integrations/google/{connection_id}/disconnect")
    assert "The Google account was disconnected" in response.text
    assert fake_google.revoked == ["refresh-1"]
    assert rows(client, Connection) == []
    assert client.post(f"/integrations/google/{connection_id}/test").status_code == 404


# Encryption


def test_secret_box_round_trip_and_wrong_key():
    token = SecretBox(b"key").encrypt({"a": 1})
    assert SecretBox(b"key").decrypt(token) == {"a": 1}
    with pytest.raises(InvalidToken):
        SecretBox(b"other").decrypt(token)


def test_secret_key_file_is_private_and_reused(tmp_path: Path):
    key = load_secret_key(None, tmp_path)
    mode = stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode)
    assert mode == 0o600
    assert load_secret_key(None, tmp_path) == key
    assert load_secret_key("from-env", tmp_path) == b"from-env"
