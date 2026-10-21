from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis_hud.app import create_app
from jarvis_hud.config import Settings
from jarvis_hud.plugins.core import CorePlugin

PASSWORD = "correct horse battery"
ORIGIN = "http://testserver"

MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[MakeClient]:
    """Start a Jarvis app on a shared data directory and return a client for it.

    Each call starts a new app, so calling it twice simulates a restart or a second device.
    """
    clients: list[TestClient] = []

    def make(**kwargs) -> TestClient:
        app = create_app(Settings(data_dir=tmp_path / "data"), plugins={"core": CorePlugin()})
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
    """A client signed in to a Jarvis that has finished setup."""
    client = make_client()
    complete_setup(client)
    return client
