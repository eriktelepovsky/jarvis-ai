from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import MakeClient, complete_setup
from sqlalchemy import select

from myorb.overview.glance import duration, focus_blocks, free_minutes, glance
from myorb.overview.models import WidgetSnapshot
from myorb.overview.snapshots import change_label, numeric
from myorb.plugins import TimelineItem
from myorb.tasks.plugin import TasksPlugin

TZ = ZoneInfo("Europe/Bratislava")
NOW = datetime(2026, 10, 22, 15, 30, tzinfo=TZ)


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute, second=second)


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


def test_focus_blocks_are_an_hour_or_more_of_the_working_day():
    items = [
        TimelineItem(at(8), at(9, 30), "Early call"),
        TimelineItem(at(10, 15), at(11), "1:1"),  # 09:30–10:15 is too short for focus
        TimelineItem(at(14), at(15), "Review"),
        TimelineItem(at(17, 30), at(19), "Dinner"),  # 15:00–17:30 is free
    ]
    early = at(7)
    assert focus_blocks(items, early) == [(at(11), at(14)), (at(15), at(17, 30))]
    # From now on, starting on a five-minute mark.
    assert focus_blocks(items, at(11, 47, 12)) == [(at(11, 50), at(14)), (at(15), at(17, 30))]
    assert focus_blocks([], at(17, 10)) == []  # less than an hour left
    assert focus_blocks([], at(18)) == []


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


def test_a_past_day_has_no_now_and_nothing_free():
    day = datetime(2026, 10, 24, 6, 30, tzinfo=ZoneInfo("Europe/Bratislava"))
    shown = glance([TimelineItem(day.replace(hour=9), day.replace(hour=10), "Standup")], day, True)
    assert shown.now is None and shown.free_minutes == 0 and shown.items[0].past


def test_lunch_is_shown_on_the_glance_but_is_no_event():
    items = [TimelineItem(at(10), at(11), "Refinement"), TimelineItem(at(14), at(15), "Call")]
    morning = at(8)
    day = glance(items, morning, lunch=(at(12), at(12, 45)))
    assert [(p.item.title, p.lunch) for p in day.items] == [
        ("Refinement", False),
        ("Lunch", True),
        ("Call", False),
    ]
    assert day.count == 2
    # Free from now until 18:00, less the two meetings and lunch.
    assert day.free_minutes == 600 - 120 - 45
    assert all(
        not p.lunch for p in glance(items, morning, day_over=True, lunch=(at(12), at(13))).items
    )


def test_the_report_keeps_lunch_as_the_calendar_does():
    from myorb.brief.routes import _lunch

    items = [TimelineItem(at(10), at(11, 30), "Refinement"), TimelineItem(at(13), at(14), "X")]
    assert _lunch(items, at(8)) == (at(12), at(12, 45))
    assert _lunch([*items, TimelineItem(at(12), at(12, 30), "Obed")], at(8)) is None
