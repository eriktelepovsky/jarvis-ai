from conftest import complete_setup
from fastapi.testclient import TestClient

from myorb.connections import store


def test_get_started_lists_the_steps_until_hidden(client: TestClient):
    page = client.get("/integrations").text
    assert "Get started" in page and "0 of 6 done" in page
    # Google's account waits for its OAuth client; Claude is the next step.
    assert 'class="setup-step is-blocked"' in page
    assert 'href="/integrations/claude">Connect Claude</a>' in page
    assert "Get started <span" in client.get("/").text

    client.post("/integrations/setup/hide")
    page = client.get("/integrations").text
    assert 'class="panel setup"' not in page and "Show the Get started list" in page
    assert "Get started <span" not in client.get("/").text

    client.post("/integrations/setup/show")
    assert 'class="panel setup"' in client.get("/integrations").text


def test_a_step_ticks_itself_once_it_works(client: TestClient):
    async def add_weather():
        async with client.app.state.sessionmaker() as db:
            await store.save_connection(
                db, client.app.state.secret_box, "weather", "Bratislava", {}, ""
            )

    client.portal.call(add_weather)
    page = client.get("/integrations").text
    assert "1 of 6 done" in page
    assert '<li class="setup-step is-done">' in page


def test_no_get_started_in_demo_mode(tmp_path):
    from myorb.app import create_app
    from myorb.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "demo", demo=True))
    with TestClient(app, headers={"Origin": "http://testserver"}) as demo:
        complete_setup(demo)
        assert 'class="panel setup"' not in demo.get("/integrations").text
