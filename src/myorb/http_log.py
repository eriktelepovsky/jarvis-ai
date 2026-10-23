"""How often myOrb asks each service: requests and rate limits (HTTP 429) per host in the last
hour, counted on the shared HTTP client. Integrations shows them, so a rate limit can be
traced to how many requests caused it."""

import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable

import httpx2

WINDOW = 60 * 60


class RequestLog:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._limited: dict[str, deque[float]] = defaultdict(deque)

    async def on_request(self, request: httpx2.Request) -> None:
        self._requests[request.url.host].append(self._clock())

    async def on_response(self, response: httpx2.Response) -> None:
        if response.status_code == 429:
            self._limited[response.request.url.host].append(self._clock())

    def hooks(self) -> dict[str, list]:
        return {"request": [self.on_request], "response": [self.on_response]}

    def _count(self, times: deque[float]) -> int:
        since = self._clock() - WINDOW
        while times and times[0] < since:
            times.popleft()
        return len(times)

    def last_hour(self, hosts: Iterable[str]) -> tuple[int, int]:
        """Requests sent to these hosts in the last hour, and how many were rate limited."""
        hosts = set(hosts)
        return (
            sum(self._count(self._requests[host]) for host in hosts if host in self._requests),
            sum(self._count(self._limited[host]) for host in hosts if host in self._limited),
        )
