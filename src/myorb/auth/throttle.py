from collections import defaultdict, deque


class LoginThrottle:
    """Limits failed sign-in attempts per client to slow down password guessing."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)

    def _prune(self, client: str, now: float) -> deque[float]:
        failures = self._failures[client]
        while failures and now - failures[0] > self.window_seconds:
            failures.popleft()
        return failures

    def is_blocked(self, client: str, now: float) -> bool:
        return len(self._prune(client, now)) >= self.max_failures

    def record_failure(self, client: str, now: float) -> None:
        self._prune(client, now).append(now)

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)
