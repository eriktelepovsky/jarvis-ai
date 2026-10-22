from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import MakeClient, complete_setup
from sqlalchemy import select

from myorb.overview.glance import duration, free_minutes, glance
from myorb.overview.models import WidgetSnapshot
from myorb.overview.snapshots import change_label, numeric
from myorb.plugins import TimelineItem
from myorb.tasks.plugin import TasksPlugin

TZ = ZoneInfo("Europe/Bratislava")
NOW = datetime(2026, 10, 22, 15, 30, tzinfo=TZ)


def at(hour: int, minute: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def test_free_minutes_until_the_end_of_the_working_day():
    items = [
        TimelineItem(at(15), at(16), "1:1"),  # already started: busy until 16:00
        TimelineItem(at(16, 30), at(17), "Call"),
        TimelineItem(at(19), at(20), "Run"),  # after 18:00, not counted
    ]
    # Free: 16:00–16:30 and 17:00–18:00
    assert free_minutes(items, NOW) == 90
    assert free_minutes([], at(19)) == 0
    assert duration(90) == "1 h 30 m" and duration(45) == "45 m" and duration(120) == "2 h"


def test_glance_lays_out_the_day():
    items = [TimelineItem(at(9), at(10), "Standup"), TimelineItem(at(6), at(6, 30), "Gym")]
    day = glance(items, NOW)
    assert day.hours[0] == (6, 0.0)  # starts at the earliest event, before 07:00
    assert [p.item.title for p in day.items] == ["Gym", "Standup"]
    assert day.items[0].past and day.items[0].above and not day.items[1].above
    assert 0 < day.now < 100
    assert day.count == 2


def test_change_labels():
    assert (numeric(3), numeric("7h12"), numeric(None), numeric(True)) == (3.0, None, None, None)
    assert [change_label(d) for d in (None, 0, 6, -2)] == ["", "±0", "▲ +6", "▼ −2"]


def test_report_shows_sources_with_change_since_yesterday(make_client: MakeClient):
    client = make_client(plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    client.post("/tasks", data={"text": "Water plants"})

    async def yesterday() -> None:
        async with client.app.state.sessionmaker() as db:
            db.add(WidgetSnapshot(day=date.today() - timedelta(days=1), key="tasks-open", value=3))
            await db.commit()

    client.portal.call(yesterday)
    report = client.get("/brief/report").text
    assert "Day at a glance" in report
    assert 'data-open="detail-tasks-open"' in report
    assert "▼ −2" in report

    async def today() -> list[WidgetSnapshot]:
        async with client.app.state.sessionmaker() as db:
            query = select(WidgetSnapshot).where(WidgetSnapshot.day == date.today())
            return list(await db.scalars(query))

    [snapshot] = client.portal.call(today)
    assert (snapshot.key, snapshot.value) == ("tasks-open", 1)
