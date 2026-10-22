from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_integrations import FakeApis

from myorb.plugins.uptime import UptimePlugin, duration, uptime_percent
from myorb.plugins.uptime.models import Incident, Monitor


class Site:
    """A website whose answer the test controls."""

    def __init__(self, apis: FakeApis, host: str) -> None:
        self.status = 200
        apis.routes[(host, "/")] = lambda request: httpx2.Response(self.status, text="ok")


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def plugin() -> UptimePlugin:
    return UptimePlugin()


@pytest.fixture
def client(
    make_client: MakeClient, apis: FakeApis, plugin: UptimePlugin, monkeypatch
) -> TestClient:
    async def no_tls(url: str):
        return datetime.now(UTC) + timedelta(days=5) if "expiring" in url else None

    monkeypatch.setattr("myorb.plugins.uptime.checks.tls_expiry", no_tls)
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"uptime": plugin})
    complete_setup(client)
    return client


def check_all(client: TestClient, plugin: UptimePlugin) -> None:
    client.portal.call(
        plugin.checker.check_all, client.app.state.sessionmaker, client.app.state.http
    )


def incidents(client: TestClient) -> list[Incident]:
    async def query() -> list[Incident]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(Incident)))

    return client.portal.call(query)


def test_monitor_goes_down_and_recovers(client: TestClient, apis: FakeApis, plugin: UptimePlugin):
    site = Site(apis, "api.example.com")
    assert 'data-open="detail-uptime-status"' not in client.get("/welcome/widgets").text
    client.post("/integrations/uptime", data={"name": "API", "url": "https://api.example.com/"})
    assert "✓ Up" in client.get("/welcome/widgets").text

    site.status = 502
    check_all(client, plugin)
    assert incidents(client) == []  # one failed check is not an outage yet
    check_all(client, plugin)
    [incident] = incidents(client)
    assert incident.ended_at is None and incident.error == "HTTP 502"
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">1</span>' in text
    assert "✕ Down · 1 min" in text
    assert "node warn" in text

    assert "API is down: HTTP 502" in client.get("/activity").text

    site.status = 200
    check_all(client, plugin)
    [incident] = incidents(client)
    assert incident.ended_at is not None
    assert "API is up again after 1 min" in client.get("/activity").text
    assert '<span class="node-value">0</span>' in client.get("/welcome/widgets").text


def test_expiring_certificate_is_flagged(client: TestClient, apis: FakeApis):
    Site(apis, "expiring.example.com")
    client.post(
        "/integrations/uptime", data={"name": "Shop", "url": "https://expiring.example.com/"}
    )
    text = client.get("/welcome/widgets").text
    assert "▲ TLS 4 d" in text or "▲ TLS 5 d" in text
    assert "1 TLS expiring" in text


def test_invalid_address_and_removal(client: TestClient, apis: FakeApis):
    response = client.post("/integrations/uptime", data={"name": "X", "url": "example.com"})
    assert response.status_code == 400
    assert "Enter the full address" in response.text
    Site(apis, "api.example.com")
    client.post("/integrations/uptime", data={"name": "API", "url": "https://api.example.com/"})
    client.post("/integrations/uptime/1/delete")
    assert "No websites are monitored yet." in client.get("/integrations/uptime").text


def test_uptime_percent():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    monitor = Monitor(created_at=now - timedelta(days=60))
    outage = Incident(
        started_at=now - timedelta(hours=7, minutes=12), ended_at=now - timedelta(hours=7)
    )
    assert round(uptime_percent(monitor, [outage], now), 3) == round(
        100 * (1 - 12 / (30 * 24 * 60)), 3
    )
    ongoing = Incident(started_at=now - timedelta(days=3))
    assert round(uptime_percent(monitor, [ongoing], now), 1) == 90.0
    assert uptime_percent(Monitor(created_at=now), [], now) == 100.0


def test_duration():
    assert duration(timedelta(seconds=20)) == "1 min"
    assert duration(timedelta(minutes=95)) == "1 h 35 min"
    assert duration(timedelta(days=2, hours=3)) == "2 d"


def test_monitors_in_groups(client: TestClient, apis: FakeApis):
    Site(apis, "api.example.com")
    Site(apis, "shop.example.com")
    Site(apis, "blog.example.com")
    data = {"name": "API", "url": "https://api.example.com/", "group": "  Transportly "}
    client.post("/integrations/uptime", data=data)
    client.post("/integrations/uptime", data={"name": "Shop", "url": "https://shop.example.com/"})
    client.post("/integrations/uptime", data={"name": "Blog", "url": "https://blog.example.com/"})
    response = client.post(
        "/integrations/uptime/2/group",
        data={"group": "Transportly"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204  # saved in place, the page is not reloaded
    page = client.get("/integrations/uptime").text
    assert '<option value="Transportly">' in page  # offered when typing a group
    assert "<title>Uptime · Orb</title>" in page
    text = client.get("/welcome/widgets").text
    assert '<span class="trunc">Transportly</span><span class="group-count">2</span>' in text
    assert '<span class="trunc">No group</span><span class="group-count">1</span>' in text
