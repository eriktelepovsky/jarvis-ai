from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis, connections

from jarvis_hud.plugins.weather import WeatherPlugin, summarize


def forecast(start: datetime, rain_at: int | None = None) -> dict:
    """A forecast for 48 hours from midnight of `start`, in UTC (offset 0)."""
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    times = [midnight + timedelta(hours=h) for h in range(48)]
    return {
        "utc_offset_seconds": 0,
        "current": {"temperature_2m": 9.4, "apparent_temperature": 6.2, "weather_code": 3},
        "daily": {
            "temperature_2m_max": [13.2, 11.0],
            "temperature_2m_min": [4.8, 3.0],
            "precipitation_probability_max": [70, 10],
        },
        "hourly": {
            "time": [t.isoformat(timespec="minutes") for t in times],
            "temperature_2m": [10.0 + h % 5 for h in range(48)],
            "precipitation_probability": [
                70 if rain_at is not None and h >= rain_at else 0 for h in range(48)
            ],
            "weather_code": [3] * 48,
        },
    }


def test_summarize_finds_rain_later_today():
    now = datetime(2026, 10, 22, 7, 42, tzinfo=UTC)
    weather = summarize(forecast(now, rain_at=14), now)
    assert (weather["temperature"], weather["feels_like"], weather["condition"]) == (
        9,
        6,
        "Overcast",
    )
    assert (weather["high"], weather["low"], weather["rain_chance"]) == (13, 5, 70)
    assert weather["rain_from"] == datetime(2026, 10, 22, 14, 0)
    assert [h["time"].hour for h in weather["hours"]] == [7, 9, 11, 13, 15, 17]


def test_summarize_without_rain_today():
    now = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
    weather = summarize(forecast(now), now)
    assert weather["rain_from"] is None
    # The daily maximum may include rain earlier in the day; only the rest of the day counts.
    assert weather["rain_chance"] == 0


@pytest.fixture
def apis() -> FakeApis:
    apis = FakeApis()
    apis.routes[("geocoding-api.open-meteo.com", "/v1/search")] = lambda r: httpx2.Response(
        200,
        json={
            "results": [
                {
                    "name": r.url.params["name"].title(),
                    "country": "Slovakia",
                    "latitude": 48.15,
                    "longitude": 17.11,
                }
            ]
        }
        if r.url.params["name"] != "atlantis"
        else {},
    )
    apis.json("api.open-meteo.com", "/v1/forecast", forecast(datetime.now(UTC), rain_at=23))
    return apis


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"weather": WeatherPlugin()})
    complete_setup(client)
    return client


def test_weather_panel(client: TestClient):
    assert "Add your place" in client.get("/panels/weather/outside").text
    response = client.post("/integrations/weather/connect", data={"place": "atlantis"})
    assert response.status_code == 400
    assert "No place called “atlantis” was found." in response.text

    client.post("/integrations/weather/connect", data={"place": "bratislava"})
    text = client.get("/panels/weather/outside").text
    assert "9°" in text and "Overcast · H 13° L 5°" in text
    assert "Bratislava, Slovakia" in text


def test_only_one_place_is_kept(client: TestClient):
    client.post("/integrations/weather/connect", data={"place": "bratislava"})
    client.post("/integrations/weather/connect", data={"place": "kosice"})
    assert [c.account for c in connections(client)] == ["Kosice, Slovakia"]
    assert 'href="/integrations/weather/connect">Connect' not in client.get("/integrations").text
