"""What myOrb spends on Claude: each response's tokens, priced when it arrives, in euros.

The Anthropic API key myOrb holds cannot read the organization's cost report (that needs an
Admin API key, which can also manage the whole organization), so myOrb counts its own
requests instead: exact for what it sent, and nothing that other apps on the same key spent.
"""

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myorb.brief.models import ClaudeUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    write_5m: float
    write_1h: float
    read: float
    output: float


# From https://platform.claude.com/docs/en/about-claude/pricing (October 2026). A model not
# listed here is counted at Claude Opus 5.5's prices, the highest myOrb offers.
PRICES = {
    "claude-opus-5-5": Price(4, 5, 8, 0.20, 20),
    "claude-sonnet-5-5": Price(2, 2.50, 4, 0.10, 10),
    "claude-haiku-5-5": Price(0.10, 0.125, 0.20, 0.01, 0.50),
}
# Claude Haiku 5.5 costs five times as much for a prompt over 100,000 tokens.
LONG_PROMPT = 100_000
HAIKU_LONG = Price(0.50, 0.625, 1, 0.05, 2.50)

ECB_RATES = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
RATE_MAX_AGE = 12 * 60 * 60


def _get(usage: Any, name: str) -> Any:
    return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)


def tokens(usage: Any) -> dict[str, int]:
    """The four kinds of tokens a response is billed for, from the SDK's Usage or its JSON."""
    creation = _get(usage, "cache_creation")
    write_5m = _get(creation, "ephemeral_5m_input_tokens") if creation else None
    write_1h = _get(creation, "ephemeral_1h_input_tokens") if creation else None
    if write_5m is None and write_1h is None:
        write_5m = _get(usage, "cache_creation_input_tokens")
    return {
        "input_tokens": _get(usage, "input_tokens") or 0,
        "cache_write_5m": write_5m or 0,
        "cache_write_1h": write_1h or 0,
        "cache_read": _get(usage, "cache_read_input_tokens") or 0,
        "output_tokens": _get(usage, "output_tokens") or 0,
    }


def cost_usd(model: str, counted: dict[str, int]) -> float:
    price = PRICES.get(model, PRICES["claude-opus-5-5"])
    prompt = sum(
        counted[k] for k in ("input_tokens", "cache_write_5m", "cache_write_1h", "cache_read")
    )
    if model == "claude-haiku-5-5" and prompt > LONG_PROMPT:
        price = HAIKU_LONG
    return (
        counted["input_tokens"] * price.input
        + counted["cache_write_5m"] * price.write_5m
        + counted["cache_write_1h"] * price.write_1h
        + counted["cache_read"] * price.read
        + counted["output_tokens"] * price.output
    ) / 1_000_000


async def record(sessionmaker: async_sessionmaker[AsyncSession], model: str, usage: Any) -> None:
    """Keep one response's tokens and price. Never fails the request it describes."""
    try:
        counted = tokens(usage)
        async with sessionmaker() as db:
            db.add(
                ClaudeUsage(
                    at=datetime.now(UTC),
                    model=model,
                    cost_usd=cost_usd(model, counted),
                    **counted,
                )
            )
            await db.commit()
    except Exception:
        logger.exception("Could not record Claude usage")


def response_hook(sessionmaker: async_sessionmaker[AsyncSession]):
    """An httpx2 response hook that records every Messages API answer the client receives.

    A streamed answer is left alone: reading it here would hold back the stream until it
    ends. Ask records its streamed answers itself, from the final message.
    """

    async def hook(response: httpx2.Response) -> None:
        if (
            response.request.method != "POST"
            or not response.request.url.path.endswith("/v1/messages")
            or response.status_code != 200
            or "text/event-stream" in response.headers.get("content-type", "")
        ):
            return
        await response.aread()
        try:
            data = response.json()
        except ValueError:
            return
        if isinstance(data, dict) and data.get("usage"):
            await record(sessionmaker, data.get("model", ""), data["usage"])

    return hook


async def last_days_usd(db: AsyncSession, days: int = 30) -> float:
    since = datetime.now(UTC) - timedelta(days=days)
    total = await db.scalar(select(func.sum(ClaudeUsage.cost_usd)).where(ClaudeUsage.at >= since))
    return float(total or 0)


class EurRate:
    """Euros per US dollar, from the European Central Bank's daily reference rates."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._rate: float | None = None
        self._fetched = 0.0

    async def usd_to_eur(self, http: httpx2.AsyncClient) -> float | None:
        if self._rate is not None and self._clock() - self._fetched < RATE_MAX_AGE:
            return self._rate
        try:
            response = await http.get(ECB_RATES, timeout=10)
            response.raise_for_status()
            # The ECB publishes how many dollars one euro buys.
            for cube in ET.fromstring(response.text).iter():
                if cube.get("currency") == "USD":
                    self._rate, self._fetched = 1 / float(cube.get("rate")), self._clock()
                    break
        except (httpx2.HTTPError, ET.ParseError, TypeError, ValueError):
            logger.warning("Could not read the ECB's euro exchange rate")
        return self._rate
