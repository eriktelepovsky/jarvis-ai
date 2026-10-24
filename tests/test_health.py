import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis

from myorb.plugins.health import HealthPlugin, sleep_text
from myorb.plugins.health.routes import parse_days


class FakeStrava:
    def __init__(self, apis: FakeApis) -> None:
        self.refreshes = 0
        self.access = "access-1"
        apis.routes[("www.strava.com", "/oauth/token")] = self.token
        apis.routes[("www.strava.com", "/api/v3/athlete/activities")] = self.activities
        apis.routes[("www.strava.com", "/oauth/deauthorize")] = lambda r: httpx2.Response(
            200, json={}
        )

    def token(self, request: httpx2.Request) -> httpx2.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert (form["client_id"], form["client_secret"]) == ("12345", "strava-secret")
        if form["grant_type"] == "authorization_code":
            return httpx2.Response(
                200,
                json={
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_at": int(time.time()) - 10,  # already expired: forces a refresh
                    "athlete": {"firstname": "Tony", "lastname": "Stark"},
                },
            )
        self.refreshes += 1
        self.access = f"access-{self.refreshes + 1}"
        return httpx2.Response(
            200,
            json={
                "access_token": self.access,
                "refresh_token": f"refresh-{self.refreshes + 1}",
                "expires_at": int(time.time()) + 3600,
            },
        )

    def activities(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("authorization") != f"Bearer {self.access}":
            return httpx2.Response(401, json={})
        start = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        return httpx2.Response(
            200,
            json=[
                {
                    "id": 99,
                    "name": "Morning run",
                    "sport_type": "Run",
                    "start_date": start,
                    "distance": 8400,
                    "moving_time": 2650,
                }
            ],
        )


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"health": HealthPlugin()})
    complete_setup(client)
    return client


def webhook_url(client: TestClient) -> str:
    client.post("/integrations/health/apple/token")
    page = client.get("/integrations/health").text
    return urlsplit(re.search(r'<code id="webhook">([^<]+)</code>', page).group(1)).path


# Apple Health webhook


def test_parse_days():
    today = datetime(2026, 10, 23).date()
    assert parse_days({"date": "2026-10-22", "steps": 8410.0, "sleep_hours": 7.2}, today)[0] == {
        "day": datetime(2026, 10, 22).date(),
        "steps": 8410,
        "sleep_minutes": 432,
        "resting_heart_rate": None,
        "active_energy": None,
    }
    days = parse_days({"days": [{"date": "2026-10-21"}, {"date": "2026-10-22"}]}, today)
    assert len(days) == 2
    # A Shortcut without a date sends today's numbers.
    assert parse_days({"steps": 1200}, today)[0]["day"] == today
    # A Shortcut that found no sleep sends a sum of 0: no sleep was measured.
    assert parse_days({"steps": 1200, "sleep_minutes": 0}, today)[0]["sleep_minutes"] is None
    # A date variable Shortcuts did not format arrives in the phone's own format.
    for body in [{}, [], {"date": "23. 10. 2026 o 12:28"}, {"date": "yesterday"}, ["x"]]:
        with pytest.raises(ValueError):
            parse_days(body, today)


def test_webhook_stores_days_without_session_or_origin(client: TestClient):
    path = webhook_url(client)
    client.cookies.clear()
    today = datetime.now().date().isoformat()
    response = client.post(path, json={"date": today, "sleep_minutes": 432}, headers={"Origin": ""})
    assert response.json() == {"ok": True, "days": 1}
    # A later call with only steps keeps the sleep.
    client.post(path, json={"date": today, "steps": 8410})
    assert client.post("/hooks/health/wrong-token", json={"date": today}).status_code == 404
    assert client.post(path, json={"date": "nope"}).status_code == 400

    complete_signin = client.post("/login", data={"password": "correct horse battery"})
    assert complete_signin.status_code == 200
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">7h12</span>' in text
    assert '<span class="node-label">Sleep</span>' in text
    assert "8,410" in text
    assert "Apple Health" in text


def test_orb_shows_the_chosen_number(client: TestClient):
    path = webhook_url(client)
    today = datetime.now().date().isoformat()
    client.post(path, json={"date": today, "steps": 8410, "sleep_minutes": 432})
    # Automatic: last night's sleep.
    assert '<span class="node-value">7h12</span>' in client.get("/welcome/widgets").text

    page = client.post("/integrations/health/orb", data={"show": "steps"}).text
    assert "The orb shows the chosen number now." in page
    assert '<option value="steps" selected>Steps today</option>' in page
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">8,410</span>' in text
    assert '<span class="node-label">Steps</span>' in text

    # Strava is not connected: its number is a dash rather than another number.
    client.post("/integrations/health/orb", data={"show": "km"})
    assert '<span class="node-value">—</span>' in client.get("/welcome/widgets").text
    assert client.post("/integrations/health/orb", data={"show": "pulse"}).status_code == 400


def test_automatic_skips_sleep_that_was_not_measured(client: TestClient):
    path = webhook_url(client)
    today = datetime.now().date().isoformat()
    client.post(path, json={"date": today, "steps": 928, "sleep_minutes": 0})
    text = client.get("/welcome/widgets").text
    # Without sleep or Strava, today's steps rather than "0h00".
    assert '<span class="node-value">928</span>' in text
    assert "0h00" not in text


def test_new_address_replaces_the_old(client: TestClient):
    old = webhook_url(client)
    new = webhook_url(client)
    assert old != new
    today = datetime.now().date().isoformat()
    assert client.post(old, json={"date": today}).status_code == 404
    assert client.post(new, json={"date": today}).status_code == 200


# Strava


def connect_strava(client: TestClient) -> httpx2.Response:
    client.post(
        "/integrations/health/strava/client",
        data={"client_id": "12345", "client_secret": "strava-secret"},
    )
    response = client.post("/integrations/health/strava/connect", follow_redirects=False)
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["redirect_uri"] == ["http://testserver/integrations/health/strava/callback"]
    assert params["scope"] == ["read,activity:read_all"]
    return client.get(
        "/integrations/health/strava/callback",
        params={"state": params["state"][0], "code": "c", "scope": "read,activity:read_all"},
    )


def test_strava_connects_refreshes_and_shows_activities(client: TestClient, apis: FakeApis):
    strava = FakeStrava(apis)
    assert 'data-open="detail-health-today"' not in client.get("/welcome/widgets").text
    response = connect_strava(client)
    assert "Strava is connected." in response.text
    assert "Tony Stark" in response.text

    text = client.get("/welcome/widgets").text
    assert "Morning run" in text
    assert "Run · 8.4 km · 44 min" in text
    # Without sleep from Apple Health the number is the week's kilometres, and says so.
    assert '<span class="node-value">8.4</span>' in text
    assert '<span class="node-label">Km · week</span>' in text
    assert strava.refreshes == 1

    # The rotated refresh token was stored and is used next time.
    async def credentials() -> dict:
        accounts = await client.app.state.connections.list("strava")
        return accounts[0].credentials

    assert client.portal.call(credentials)["refresh_token"] == "refresh-2"


def test_strava_needs_activity_permission(client: TestClient, apis: FakeApis):
    FakeStrava(apis)
    client.post(
        "/integrations/health/strava/client",
        data={"client_id": "12345", "client_secret": "strava-secret"},
    )
    response = client.post("/integrations/health/strava/connect", follow_redirects=False)
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    response = client.get(
        "/integrations/health/strava/callback",
        params={"state": state, "code": "c", "scope": "read"},
    )
    assert "permission to read your activities" in response.text


def test_strava_callback_rejects_unknown_state(client: TestClient, apis: FakeApis):
    FakeStrava(apis)
    response = client.get(
        "/integrations/health/strava/callback", params={"state": "forged", "code": "c"}
    )
    assert "expired or did not start here" in response.text
    assert "forged" not in response.text


def test_disconnect_strava(client: TestClient, apis: FakeApis):
    FakeStrava(apis)
    connect_strava(client)
    response = client.post("/integrations/health/strava/1/disconnect")
    assert "Strava was disconnected." in response.text


def test_integrations_page_links_health(client: TestClient):
    assert 'href="/integrations/health"' in client.get("/integrations").text


def test_sleep_text():
    assert (sleep_text(None), sleep_text(432), sleep_text(65)) == ("—", "7h12", "1h05")
