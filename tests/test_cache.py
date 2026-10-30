import asyncio

import pytest

from myorb.cache import Cache, refreshing


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def counter():
    calls = {"n": 0}

    async def fetch() -> int:
        calls["n"] += 1
        return calls["n"]

    return fetch, calls


def test_fresh_values_come_from_the_cache():
    async def run() -> None:
        cache = Cache(Clock())
        fetch, calls = counter()
        assert (await cache.get("k", fetch, fresh=60, max_age=600)).value == 1
        assert (await cache.get("k", fetch, fresh=60, max_age=600)).value == 1
        assert calls["n"] == 1

    asyncio.run(run())


def test_refreshing_fetches_now():
    async def run() -> None:
        cache = Cache(Clock())
        fetch, calls = counter()
        await cache.get("k", fetch, fresh=60, max_age=600)
        token = refreshing.set(True)
        try:
            assert (await cache.get("k", fetch, fresh=60, max_age=600)).value == 2
        finally:
            refreshing.reset(token)
        assert (await cache.get("k", fetch, fresh=60, max_age=600)).value == 2

    asyncio.run(run())


def test_refreshing_keeps_the_last_value_when_it_fails():
    async def run() -> None:
        cache = Cache(Clock())
        fetch, _ = counter()
        await cache.get("k", fetch, fresh=60, max_age=600)

        async def broken() -> int:
            raise RuntimeError("down")

        token = refreshing.set(True)
        try:
            cached = await cache.get("k", broken, fresh=60, max_age=600)
        finally:
            refreshing.reset(token)
        assert cached.value == 1 and isinstance(cached.error, RuntimeError)

    asyncio.run(run())


def test_refresh_button(make_client):
    from conftest import complete_setup

    from myorb.tasks.plugin import TasksPlugin

    client = make_client(plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    assert 'hx-post="/welcome/widgets/tasks/open/refresh"' in client.get("/welcome/widgets").text
    response = client.post("/welcome/widgets/tasks/open/refresh")
    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "myorb:refresh"
    assert client.post("/welcome/widgets/tasks/nothing/refresh").status_code == 404


def test_failures_are_kept_until_a_fetch_succeeds():
    cache = Cache()

    async def failing() -> int:
        raise ValueError("expired")

    async def run() -> None:
        with pytest.raises(ValueError):
            await cache.get("a:1:x", failing, fresh=60, max_age=600)
        assert [str(e) for e in cache.failures("a:1:")] == ["expired"]
        assert cache.failures("a:2:") == []
        await cache.get("a:1:x", fetch_one, fresh=60, max_age=600)
        assert cache.failures("a:1:") == []

    async def fetch_one() -> int:
        return 1

    asyncio.run(run())
