"""Checking monitors: an HTTP request every minute, and the TLS certificate every hour. Those
from Uptime Kuma are not checked: their state is read from Kuma (kuma.py)."""

import asyncio
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.activity.store import event
from myorb.connections.providers import ConnectedAccount, ProviderError
from myorb.plugins.uptime import kuma
from myorb.plugins.uptime.models import Incident, Monitor

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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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

    async def apply(
        self,
        db: AsyncSession,
        monitor: Monitor,
        result: dict,
        failures_before_down: int = FAILURES_BEFORE_DOWN,
    ) -> None:
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
                    minutes = max(
                        1, round((now - _aware(incident.started_at)).total_seconds() / 60)
                    )
                    db.add(
                        event(f"{monitor.name} is up again after {minutes} min", "ok", monitor.url)
                    )
            monitor.up, monitor.error = True, None
            return
        self.failures[monitor.id] = self.failures.get(monitor.id, 0) + 1
        monitor.error = result["error"]
        if self.failures[monitor.id] >= failures_before_down and monitor.up is not False:
            monitor.up = False
            db.add(Incident(monitor_id=monitor.id, started_at=now, error=result["error"]))
            db.add(event(f"{monitor.name} is down: {result['error']}", "fail", monitor.url))

    async def check(self, db: AsyncSession, http: httpx2.AsyncClient, monitor: Monitor) -> None:
        await self.apply(db, monitor, await self.probe(http, monitor))

    async def check_all(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx2.AsyncClient,
        kumas: list[ConnectedAccount] | None = None,
    ) -> None:
        """Check myOrb's own monitors, and read those of each connected Uptime Kuma."""
        if kumas is not None:
            await self.read_kumas(sessionmaker, http, kumas)
        async with sessionmaker() as db:
            monitors = list(await db.scalars(select(Monitor).where(Monitor.kuma_key.is_(None))))
            # Network checks run in parallel; the database session is used one at a time.
            results = await asyncio.gather(*(self.probe(http, m) for m in monitors))
            for monitor, result in zip(monitors, results, strict=True):
                await self.apply(db, monitor, result)
            await db.commit()

    async def read_kumas(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx2.AsyncClient,
        kumas: list[ConnectedAccount],
    ) -> None:
        """The monitors of each connected Kuma, added, updated or removed as they are there.
        A Kuma that cannot be read keeps its monitors as they were."""
        read: dict[int, list[dict[str, Any]]] = {}
        for account in kumas:
            try:
                read[account.id] = await kuma.fetch(http, account.credentials)
            except ProviderError as error:
                logger.warning("Uptime Kuma %s could not be read: %s", account.account, error)
        connected = {account.id: account for account in kumas}
        async with sessionmaker() as db:
            known = list(await db.scalars(select(Monitor).where(Monitor.kuma_key.is_not(None))))
            by_key = {(m.kuma_connection, m.kuma_key): m for m in known}
            for monitor in known:
                gone = monitor.kuma_connection not in connected
                removed = monitor.kuma_connection in read and monitor.kuma_key not in {
                    found["key"] for found in read[monitor.kuma_connection]
                }
                if gone or removed:
                    await db.delete(monitor)
            await db.flush()
            for connection_id, found in read.items():
                base = connected[connection_id].credentials["url"]
                for item in found:
                    monitor = by_key.get((connection_id, item["key"]))
                    if monitor is None:
                        monitor = Monitor(
                            name=item["name"][:100],
                            url=base,
                            created_at=datetime.now(UTC),
                            kuma_connection=connection_id,
                            kuma_key=item["key"][:200],
                        )
                        db.add(monitor)
                        await db.flush()
                    monitor.name = item["name"][:100]
                    address = item["address"]
                    monitor.url = (address if "://" in address else base)[:500]
                    await self._apply_kuma(db, monitor, item)
            await db.commit()

    async def _apply_kuma(self, db: AsyncSession, monitor: Monitor, item: dict[str, Any]) -> None:
        if item["status"] not in (kuma.UP, kuma.DOWN):
            # Pending or in maintenance: as it was, until Kuma decides.
            monitor.checked_at = datetime.now(UTC)
            return
        up = item["status"] == kuma.UP
        result: dict[str, Any] = {
            "up": up,
            "status": None,
            "ms": item["ms"],
            "error": None if up else "Down in Uptime Kuma",
        }
        if item["cert_days"] is not None and item["cert_days"] > 0:
            result["tls"] = datetime.now(UTC) + timedelta(days=item["cert_days"])
        # Kuma counts its own retries before it calls a monitor down.
        await self.apply(db, monitor, result, failures_before_down=1)

    async def run(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx2.AsyncClient,
        kumas: Callable[[], Awaitable[list[ConnectedAccount]]] | None = None,
        interval: float = 60,
    ) -> None:
        while True:
            try:
                await self.check_all(sessionmaker, http, await kumas() if kumas else None)
            except Exception:
                logger.exception("Uptime checks failed")
            await asyncio.sleep(interval)
