import base64

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_integrations import FakeApis

from myorb.plugins.uptime import UptimePlugin
from myorb.plugins.uptime.kuma import parse_metrics
from myorb.plugins.uptime.models import Incident, Monitor

KEY = "uk1_secret"


def metric(name: str, value: float, **labels: str) -> str:
    text = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"monitor_{name}{{{text}}} {value}"


class Kuma:
    """An Uptime Kuma's /metrics, its monitors controlled by the test."""

    def __init__(self, apis: FakeApis) -> None:
        self.monitors = {
            "Shop": {"status": 1, "ms": 120, "url": "https://shop.example.com", "cert": 40},
            "DB": {"status": 1, "ms": 3, "url": "null", "host": "db.internal", "port": "5432"},
        }
        apis.routes[("kuma.example.com", "/metrics")] = self.answer

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        expected = "Basic " + base64.b64encode(f":{KEY}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx2.Response(401, text="Unauthorized")
        lines = [
            "# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)"
        ]
        for name, m in self.monitors.items():
            labels = {
                "monitor_name": name,
                "monitor_type": "http",
                "monitor_url": m["url"],
                "monitor_hostname": m.get("host", "null"),
                "monitor_port": m.get("port", "null"),
            }
            lines.append(metric("status", m["status"], **labels))
            lines.append(metric("response_time", m["ms"], **labels))
            if "cert" in m:
                lines.append(metric("cert_days_remaining", m["cert"], **labels))
        return httpx2.Response(200, text="\n".join(lines))


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def plugin() -> UptimePlugin:
    return UptimePlugin()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis, plugin: UptimePlugin) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"uptime": plugin})
    complete_setup(client)
    return client


def read(client: TestClient, plugin: UptimePlugin) -> None:
    async def run() -> None:
        kumas = await client.app.state.connections.list("uptime_kuma")
        await plugin.checker.check_all(client.app.state.sessionmaker, client.app.state.http, kumas)

    client.portal.call(run)


def rows(client: TestClient, model: type) -> list:
    async def query() -> list:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(model)))

    return client.portal.call(query)


def test_parse_metrics():
    text = "\n".join(
        [
            "# TYPE monitor_status gauge",
            metric(
                "status",
                1,
                monitor_name='Say "hi"',
                monitor_type="http",
                monitor_url="https://a.example",
            ),
            metric(
                "response_time",
                -1,
                monitor_name='Say "hi"',
                monitor_type="http",
                monitor_url="https://a.example",
            ),
            metric(
                "status",
                0,
                monitor_id="7",
                monitor_name="Ping",
                monitor_type="ping",
                monitor_url="https://",
                monitor_hostname="10.0.0.1",
                monitor_port="null",
            ),
        ]
    ).replace('Say "hi"', 'Say \\"hi\\"')
    hi, ping = parse_metrics(text)
    assert hi == {
        "key": 'Say "hi"',
        "name": 'Say "hi"',
        "type": "http",
        "address": "https://a.example",
        "status": 1,
        "ms": None,
        "cert_days": None,
    }
    assert (ping["key"], ping["address"], ping["status"]) == ("7", "10.0.0.1", 0)


def test_kuma_monitors_show_with_myorbs_own(
    client: TestClient, apis: FakeApis, plugin: UptimePlugin
):
    kuma = Kuma(apis)
    wrong = client.post(
        "/integrations/uptime_kuma/connect", data={"url": "kuma.example.com", "api_key": "nope"}
    )
    assert wrong.status_code == 400 and "rejected the credentials" in wrong.text
    client.post(
        "/integrations/uptime_kuma/connect", data={"url": "kuma.example.com/", "api_key": KEY}
    )
    assert "Read the monitors (2 monitors)" in client.get("/integrations").text

    read(client, plugin)
    text = client.get("/welcome/widgets").text
    assert "shop.example.com · 120 ms" in text and "Uptime Kuma" in text
    # A monitor without a web address links to the Kuma.
    assert {m.name: m.url for m in rows(client, Monitor)}["DB"] == "https://kuma.example.com"
    page = client.get("/integrations/uptime").text
    assert page.count("Uptime Kuma</span>") == 2 and "Remove</button>" not in page

    # Kuma's DOWN is an outage at once: Kuma counted its retries already.
    kuma.monitors["Shop"]["status"] = 0
    read(client, plugin)
    [incident] = rows(client, Incident)
    assert incident.ended_at is None
    assert "Shop is down: Down in Uptime Kuma" in client.get("/activity").text
    # Pending is not decided yet: it stays down until Kuma says up.
    kuma.monitors["Shop"]["status"] = 2
    read(client, plugin)
    assert rows(client, Incident)[0].ended_at is None
    kuma.monitors["Shop"]["status"] = 1
    read(client, plugin)
    assert rows(client, Incident)[0].ended_at is not None

    # Gone in Kuma, gone here; and all of them once the Kuma is disconnected.
    del kuma.monitors["DB"]
    read(client, plugin)
    assert [m.name for m in rows(client, Monitor)] == ["Shop"]
    assert client.post("/integrations/uptime/1/delete").status_code == 400
    client.post("/integrations/uptime_kuma/1/disconnect")
    read(client, plugin)
    assert rows(client, Monitor) == []


def test_an_unreachable_kuma_keeps_its_monitors(
    client: TestClient, apis: FakeApis, plugin: UptimePlugin
):
    Kuma(apis)
    client.post(
        "/integrations/uptime_kuma/connect", data={"url": "kuma.example.com", "api_key": KEY}
    )
    read(client, plugin)
    apis.routes[("kuma.example.com", "/metrics")] = lambda request: httpx2.Response(502)
    read(client, plugin)
    assert len(rows(client, Monitor)) == 2
