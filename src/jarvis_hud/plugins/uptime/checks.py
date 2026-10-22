"""Checking monitors: an HTTP request every minute, and the TLS certificate every hour."""

import asyncio
import logging
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_hud.plugins.uptime.models import Incident, Monitor

logger = logging.getLogger(__name__)

TIMEOUT = 10
# A monitor counts as down after this many failed checks in a row, to ignore single blips.
FAILURES_BEFORE_DOWN = 2
TLS_EVERY = 60 * 60


async def check_url(
    http: httpx2.AsyncClient, url: str
) -> tuple[bool, int | None, int | None, str | None]:
    """(up, status code, response time in ms, error)"""
    start = time.perf_counter()
    try:
        response = await http.get(url, follow_redirects=True, timeout=TIMEOUT)
    except httpx2.TimeoutException:
        return False, None, None, f"No answer within {TIMEOUT} s"
    except httpx2.HTTPError:
        return False, None, None, "Cannot connect"
    elapsed = round((time.perf_counter() - start) * 1000)
    if response.status_code >= 400:
        return False, response.status_code, elapsed, f"HTTP {response.status_code}"
    return True, response.status_code, elapsed, None


async def tls_expiry(url: str) -> datetime | None:
    """When the site's TLS certificate expires, or None for http:// or when it cannot be read."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                parts.hostname,
                parts.port or 443,
                ssl=ssl.create_default_context(),
                server_hostname=parts.hostname,
            ),
            TIMEOUT,
        )
    except (OSError, TimeoutError, ssl.SSLError):
        return None
    try:
        certificate = writer.get_extra_info("peercert") or {}
        not_after = certificate.get("notAfter")
        return (
            datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC) if not_after else None
        )
    finally:
        writer.close()


class Checker:
    """Checks all monitors and records incidents. Keeps short-lived state in memory."""

    def __init__(self) -> None:
        self.failures: dict[int, int] = {}
        self.tls_checked: dict[int, float] = {}

    async def probe(self, http: httpx2.AsyncClient, monitor: Monitor) -> dict:
        """Check a monitor over the network; touches no database."""
        up, status, ms, error = await check_url(http, monitor.url)
        result = {"up": up, "status": status, "ms": ms, "error": error}
        if time.time() - self.tls_checked.get(monitor.id, 0) > TLS_EVERY:
            self.tls_checked[monitor.id] = time.time()
            result["tls"] = await tls_expiry(monitor.url)
        return result

    async def apply(self, db: AsyncSession, monitor: Monitor, result: dict) -> None:
        """Record a check: the monitor's state, and an incident when it goes down or comes back."""
        now = datetime.now(UTC)
        monitor.status_code, monitor.response_ms, monitor.checked_at = (
            result["status"],
            result["ms"],
            now,
        )
        if "tls" in result:
            monitor.tls_expires_at = result["tls"]
        if result["up"]:
            self.failures[monitor.id] = 0
            if monitor.up is False:
                incident = await db.scalar(
                    select(Incident).where(
                        Incident.monitor_id == monitor.id, Incident.ended_at.is_(None)
                    )
                )
                if incident:
                    incident.ended_at = now
            monitor.up, monitor.error = True, None
            return
        self.failures[monitor.id] = self.failures.get(monitor.id, 0) + 1
        monitor.error = result["error"]
        if self.failures[monitor.id] >= FAILURES_BEFORE_DOWN and monitor.up is not False:
            monitor.up = False
            db.add(Incident(monitor_id=monitor.id, started_at=now, error=result["error"]))

    async def check(self, db: AsyncSession, http: httpx2.AsyncClient, monitor: Monitor) -> None:
        await self.apply(db, monitor, await self.probe(http, monitor))

    async def check_all(
        self, sessionmaker: async_sessionmaker[AsyncSession], http: httpx2.AsyncClient
    ) -> None:
        async with sessionmaker() as db:
            monitors = list(await db.scalars(select(Monitor)))
            # Network checks run in parallel; the database session is used one at a time.
            results = await asyncio.gather(*(self.probe(http, m) for m in monitors))
            for monitor, result in zip(monitors, results, strict=True):
                await self.apply(db, monitor, result)
            await db.commit()

    async def run(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx2.AsyncClient,
        interval: float = 60,
    ) -> None:
        while True:
            try:
                await self.check_all(sessionmaker, http)
            except Exception:
                logger.exception("Uptime checks failed")
            await asyncio.sleep(interval)
