"""Weather from Open-Meteo: free, no API key, data licensed CC BY 4.0."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2

from myorb.connections.providers import (
    ConnectedAccount,
    Field,
    Provider,
    ProviderError,
    Verified,
    request_json,
)
from myorb.plugins.base import BriefSection, Panel, Plugin, WidgetContext
from myorb.plugins.connection_data import load_connected

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
RAIN_LIKELY = 50  # percent
HOURS_SHOWN = 6
HOUR_STEP = 2

# WMO weather interpretation codes, as Open-Meteo documents them.
CONDITIONS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


class Place(Provider):
    id = "weather"
    name = "Weather"
    logo = "°C"
    summary = "Today's forecast and rain by the hour for your place, from Open-Meteo."
    instructions = "place.html"
    multiple = False
    api_hosts = ("api.open-meteo.com", "geocoding-api.open-meteo.com")
    fields = [Field("place", "Place", "text", "e.g. Bratislava")]

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        data = await request_json(
            http,
            "GET",
            GEOCODING,
            "Open-Meteo",
            params={"name": values["place"], "count": 1, "language": "en", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise ProviderError(
                f"No place called “{values['place']}” was found. Try a nearby city."
            )
        found = results[0]
        label = ", ".join(p for p in (found.get("name"), found.get("country")) if p)
        credentials = {
            "place": label,
            "latitude": found["latitude"],
            "longitude": found["longitude"],
        }
        return Verified(label, credentials)


async def fetch_forecast(http: httpx2.AsyncClient, credentials: dict[str, Any]) -> dict[str, Any]:
    return await request_json(
        http,
        "GET",
        FORECAST,
        "Open-Meteo",
        params={
            "latitude": credentials["latitude"],
            "longitude": credentials["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code",
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 2,
        },
    )


def summarize(forecast: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Today's weather from an Open-Meteo forecast, in the place's local time."""
    current = forecast.get("current", {})
    daily = forecast.get("daily", {})
    hourly = forecast.get("hourly", {})
    times = [datetime.fromisoformat(t) for t in hourly.get("time", [])]
    # Forecast times are the place's local time; compare them with now at the place.
    offset = timedelta(seconds=forecast.get("utc_offset_seconds", 0))
    local_now = (now.astimezone(UTC) + offset).replace(
        tzinfo=None, minute=0, second=0, microsecond=0
    )
    hours = [
        {
            "time": t,
            "temperature": round(hourly["temperature_2m"][i]),
            "rain": hourly["precipitation_probability"][i] or 0,
        }
        for i, t in enumerate(times)
        if local_now <= t < local_now + timedelta(hours=24)
    ]
    end_of_day = local_now.replace(hour=23)
    rest_of_day = [h for h in hours if h["time"] <= end_of_day]
    rain = next((h for h in rest_of_day if h["rain"] >= RAIN_LIKELY), None)
    return {
        "temperature": round(current.get("temperature_2m", 0)),
        "feels_like": round(current.get("apparent_temperature", 0)),
        "condition": CONDITIONS.get(current.get("weather_code"), "—"),
        "high": round(daily.get("temperature_2m_max", [0])[0]),
        "low": round(daily.get("temperature_2m_min", [0])[0]),
        # For the rest of the day: rain earlier this morning no longer matters.
        "rain_chance": max((h["rain"] for h in rest_of_day), default=0),
        "rain_from": rain["time"] if rain else None,
        "hours": hours[: HOURS_SHOWN * HOUR_STEP : HOUR_STEP],
    }


class WeatherPlugin(Plugin):
    """Weather in the side column and the brief."""

    id = "weather"
    name = "Open-Meteo"
    templates_dir = Path(__file__).parent / "templates"
    position = 50

    def __init__(self) -> None:
        self.provider = Place()

    def connections(self) -> list[Provider]:
        return [self.provider]

    def panels(self) -> list[Panel]:
        return [Panel(id="outside", title="Outside", template="outside.html", load=self._panel)]

    async def _weather(self, context: WidgetContext) -> dict[str, Any] | None:
        async def fetch(provider: Provider, account: ConnectedAccount) -> dict[str, Any]:
            return await fetch_forecast(context.http, account.credentials)

        results = await load_connected(
            context, [self.provider], "forecast", fetch, fresh=30 * 60, max_age=3 * 60 * 60
        )
        if not results:
            return None
        result = results[0]
        weather = summarize(result.data, context.now) if result.data is not None else None
        return {"place": result.account, "weather": weather, "error": result.error}

    async def _panel(self, context: WidgetContext) -> dict[str, Any]:
        data = await self._weather(context)
        return {"connected": False} if data is None else {"connected": True, **data}

    async def brief(self, context: WidgetContext) -> BriefSection | None:
        data = await self._weather(context)
        if not data or not data["weather"]:
            return None
        w = data["weather"]
        lines = [
            f"{data['place']}: {w['condition']}, {w['temperature']}° (feels {w['feels_like']}°), "
            f"today {w['low']}° to {w['high']}°, rain chance for the rest of the day "
            f"up to {w['rain_chance']}%."
        ]
        if w["rain_from"]:
            lines.append(f"Rain likely from {w['rain_from']:%H:%M}.")
        return BriefSection("Weather", lines)
