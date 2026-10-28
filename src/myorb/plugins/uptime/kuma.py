"""Uptime Kuma: the monitors of the user's own Uptime Kuma, shown with myOrb's.

Kuma checks them; myOrb reads their state every minute from Kuma's /metrics (Prometheus text,
with an API key from Kuma's Settings → API Keys as the password of basic auth) and keeps them
as monitors of its own that it does not check itself. So outages, the 30 days' uptime, the
brief and the activity work the same for both.
"""

import re
from typing import Any
from urllib.parse import urlsplit

import httpx2

from myorb.connections.providers import Field, Provider, ProviderError, Verified, status_error

SERVICE = "Uptime Kuma"
# Kuma's monitor_status: 0 down, 1 up, 2 pending (failing, not yet down), 3 maintenance.
DOWN, UP = 0, 1
LINE = re.compile(r"^(monitor_[a-z_]+)\{(.*)\}\s+(\S+)$")
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.removesuffix("/metrics")


def _value(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _address(labels: dict[str, str]) -> str:
    """Where the monitor points: its URL, else its host (and port), else nothing."""
    url = labels.get("monitor_url", "")
    if url not in ("", "null", "https://", "http://"):
        return url
    host = labels.get("monitor_hostname", "")
    if host in ("", "null"):
        return ""
    port = labels.get("monitor_port", "")
    return f"{host}:{port}" if port not in ("", "null") else host


def parse_metrics(text: str) -> list[dict[str, Any]]:
    """Each monitor in Kuma's /metrics: {"key", "name", "type", "address", "status", "ms",
    "cert_days"}, in Kuma's order."""
    monitors: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        metric, labels_text, value = match.groups()
        labels = {k: v.replace('\\"', '"') for k, v in LABEL.findall(labels_text)}
        name = labels.get("monitor_name")
        if not name:
            continue
        # Kuma 2 names each monitor's id; before it, the name is what tells them apart.
        key = labels.get("monitor_id") or name
        monitor = monitors.setdefault(
            key,
            {
                "key": key,
                "name": name,
                "type": labels.get("monitor_type", ""),
                "address": _address(labels),
                "status": None,
                "ms": None,
                "cert_days": None,
            },
        )
        number = _value(value)
        if metric == "monitor_status" and number is not None:
            monitor["status"] = int(number)
        elif metric == "monitor_response_time" and number is not None and number >= 0:
            monitor["ms"] = round(number)
        elif metric == "monitor_cert_days_remaining" and number is not None:
            monitor["cert_days"] = int(number)
    return [m for m in monitors.values() if m["status"] is not None]


async def fetch(http: httpx2.AsyncClient, credentials: dict[str, Any]) -> list[dict[str, Any]]:
    """The monitors of a Kuma. Raises ProviderError with a readable message."""
    url = f"{credentials['url']}/metrics"
    try:
        response = await http.get(url, auth=("", credentials["api_key"]), timeout=10)
    except httpx2.HTTPError as error:
        raise ProviderError(f"{SERVICE} could not be reached at {credentials['url']}.") from error
    if response.status_code >= 400:
        raise status_error(response, SERVICE)
    if "monitor_status" not in response.text:
        raise ProviderError(
            f"{credentials['url']}/metrics did not answer with Uptime Kuma's metrics. "
            "Check the address."
        )
    return parse_metrics(response.text)


class UptimeKuma(Provider):
    id = "uptime_kuma"
    name = "Uptime Kuma"
    logo = "UK"
    summary = "Monitors from your Uptime Kuma: it checks them, myOrb shows them with its own."
    instructions = "kuma.html"
    account_noun = "Kuma"
    fields = [
        Field("url", "Address", "url", "https://status.example.com"),
        Field(
            "api_key",
            "API key",
            "password",
            help="In Uptime Kuma: Settings → API Keys → Add API Key.",
        ),
    ]

    def hosts(self, credentials: dict[str, Any]) -> set[str]:
        return {urlsplit(credentials["url"]).hostname or ""}

    async def verify(self, http: httpx2.AsyncClient, values: dict[str, str]) -> Verified:
        credentials = {"url": base_url(values["url"]), "api_key": values["api_key"].strip()}
        monitors = await fetch(http, credentials)
        count = len(monitors)
        capability = {
            "id": "metrics",
            "label": f"Read the monitors ({count} {'monitor' if count == 1 else 'monitors'})",
            "ok": True,
        }
        return Verified(urlsplit(credentials["url"]).hostname or SERVICE, credentials, [capability])
