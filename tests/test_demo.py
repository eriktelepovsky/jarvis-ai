import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from conftest import ORIGIN, complete_setup
from fastapi.testclient import TestClient

from myorb import demo
from myorb.app import create_app
from myorb.config import Settings
from myorb.db import create_engine, create_sessionmaker, run_migrations
from myorb.tasks.store import add_task, open_tasks
from myorb.web import routes


class EightOClock(datetime):
    """The dashboard's clock at 08:00 on a demo day, before the design review."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 28, 8, 0, tzinfo=tz)


@pytest.fixture
def demo_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "datetime", EightOClock)
    app = create_app(Settings(data_dir=tmp_path / "data", demo=True, timezone="UTC"))
    with TestClient(app, headers={"Origin": ORIGIN}) as client:
        complete_setup(client)
        yield client


def lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_demo_shows_a_made_up_day(demo_client: TestClient):
    page = demo_client.get("/").text
    assert "· demo</button>" in page
    assert "Design review: Mark 85" in demo_client.get("/panels/calendar/today").text
    assert "Book flight to Monaco" in demo_client.get("/panels/tasks/today").text
    assert "Pepper Potts" in demo_client.get("/welcome/widget/demo/unread").text

    demo_client.post("/brief/generate")
    report = demo_client.get("/brief/report").text
    assert "the Mark 85 design review starts at 09:00" in report
    assert "07:48" in report
    assert "Three things need you before 09:00." in demo_client.get("/brief/headline").text
    assert "leave at 12:08 for lunch with Pepper" in report


def test_demo_asks_without_claude(demo_client: TestClient):
    asked = lines(
        demo_client.post(
            "/ask", json={"messages": [{"role": "user", "content": "Plan my day: what first?"}]}
        )
    )
    text = "".join(line.get("text", "") for line in asked)
    assert "Design review: Mark 85" in text and "12:08" in text
    proposal = next(line["proposal"] for line in asked if "proposal" in line)

    done = lines(
        demo_client.post(
            "/ask/decide",
            json={"conversation": asked[0]["conversation"], "approved": [proposal["id"]]},
        )
    )
    assert done[0]["done"]["message"] == "Task added: Fix the failing tests on PR #318"
    assert "Fix the failing tests on PR #318" in demo_client.get("/panels/tasks/today").text

    other = lines(
        demo_client.post("/ask", json={"messages": [{"role": "user", "content": "Who is Rhodey?"}]})
    )
    assert "written in advance" in "".join(line.get("text", "") for line in other)


def test_the_plan_takes_what_is_left_of_the_day():
    async def plan(hour: int) -> str:
        now = datetime(2026, 10, 28, hour, 0)
        answer = [json.loads(line) async for line in demo.answer("plan my day", now)]
        return "".join(line.get("text", "") for line in answer)

    morning, afternoon, night = (asyncio.run(plan(h)) for h in (8, 15, 22))
    assert "**Now – 09:00:** fix the 2 failing tests" in morning
    assert "**Now – 18:00:** your free afternoon" in afternoon and "Design review" not in afternoon
    assert "Your day is done" in night


@pytest.fixture
def public_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "datetime", EightOClock)
    app = create_app(Settings(data_dir=tmp_path / "data", public_demo=True, timezone="UTC"))
    with TestClient(app, headers={"Origin": ORIGIN}) as client:
        yield client


def test_the_public_demo_is_open_and_changes_nothing(public_client: TestClient):
    page = public_client.get("/")
    assert page.status_code == 200 and "Public demo" in page.text and "Sign out" not in page.text
    assert "Tony" in page.text and "J.A.R.V.I.S." in page.text
    assert public_client.get("/setup", follow_redirects=False).headers["location"] == "/"
    assert public_client.get("/login", follow_redirects=False).headers["location"] == "/"

    refused = public_client.post("/integrations/claude", data={"api_key": "sk-ant-x"})
    assert refused.status_code == 403 and refused.headers["X-Myorb-Demo"] == "read-only"
    assert public_client.post("/tasks", data={"title": "Hello"}).status_code == 403
    # Ask works, and its task is added once, however many visitors say yes.
    for _ in range(2):
        question = {"messages": [{"role": "user", "content": "Plan my day"}]}
        asked = lines(public_client.post("/ask", json=question))
        proposal = next(line["proposal"] for line in asked if "proposal" in line)
        public_client.post("/ask/decide", json={"approved": [proposal["id"]]})
    tasks = public_client.get("/panels/tasks/today").text
    assert tasks.count(">Fix the failing tests on PR #318<") == 1


def test_the_public_demo_starts_each_day_afresh(tmp_path: Path):
    async def run() -> list[str]:
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}")
        await run_migrations(engine)
        sessionmaker = create_sessionmaker(engine)
        now = datetime(2026, 10, 28, 8, 0)
        await demo.seed(sessionmaker, now)
        async with sessionmaker() as db:
            await add_task(db, "Visitor's task", now.date())
        await demo.seed(sessionmaker, now.replace(day=29), fresh=True)
        async with sessionmaker() as db:
            tasks = [t.title for t in await open_tasks(db)]
        await engine.dispose()
        return tasks

    tasks = asyncio.run(run())
    assert "Visitor's task" not in tasks and "Book flight to Monaco" in tasks


def test_public_demo_turns_demo_on():
    assert Settings(public_demo=True).demo
