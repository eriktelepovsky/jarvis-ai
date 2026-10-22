from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from myorb.app import create_app
from myorb.config import Settings
from myorb.plugins import Plugin

PASSWORD = "correct horse battery"
ORIGIN = "http://testserver"

MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[MakeClient]:
    """Start a myOrb app on a shared data directory and return a client for it.

    Each call starts a new app, so calling it twice simulates a restart or a second device.
    """
    clients: list[TestClient] = []

    def make(
        transport: httpx2.AsyncBaseTransport | None = None,
        plugins: dict[str, Plugin] | None = None,
        **kwargs,
    ) -> TestClient:
        settings = Settings(data_dir=tmp_path / "data")
        plugins = {} if plugins is None else plugins
        app = create_app(settings, plugins=plugins, http_transport=transport)
        client = TestClient(app, headers={"Origin": ORIGIN}, **kwargs)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def complete_setup(client: TestClient, password: str = PASSWORD) -> None:
    token = client.app.state.auth.setup_token
    response = client.post(
        "/setup",
        data={"password": password, "password_confirmation": password, "token": token},
    )
    assert response.status_code == 200
    assert response.url.path == "/"


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    """A client signed in to a myOrb that has finished setup."""
    client = make_client()
    complete_setup(client)
    return client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
