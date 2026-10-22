from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient

from myorb.activity.changes import WidgetWatch
from myorb.plugins import Widget, WidgetData
from myorb.tasks.plugin import TasksPlugin


def test_tasks_appear_in_the_activity(make_client: MakeClient):
    client: TestClient = make_client(plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    assert "Nothing has happened" in client.get("/activity").text
    client.post("/tasks", data={"text": "Water <plants>"})
    client.post("/tasks/1/done")
    text = client.get("/activity").text
    assert "Task added: Water &lt;plants&gt;" in text and "Done: Water &lt;plants&gt;" in text
    assert text.index("Done:") < text.index("Task added:")  # newest first
    assert 'hx-get="/activity"' in client.get("/").text


def test_widget_changes_are_noted(make_client: MakeClient):
    client: TestClient = make_client()
    complete_setup(client)
    watch = WidgetWatch()
    widget = Widget(id="inbox", label="Unread", title="Email", icon="", load=None)  # type: ignore[arg-type]

    def note(data: WidgetData) -> None:
        item = {"key": "gmail-inbox", "widget": widget, "data": data}
        client.portal.call(watch.note, client.app.state.sessionmaker, [item])

    note(WidgetData(value=10))  # the first load only remembers
    note(WidgetData(value=16))
    note(WidgetData(value=12))  # fewer: nothing to say
    note(WidgetData(value=None, message="Google is not reachable.", message_tone="fail"))
    note(WidgetData(value=12))
    text = client.get("/activity").text
    assert "Email · 6 new · unread" in text
    assert "Email: Google is not reachable." in text
    assert "Email works again" in text
    assert text.count('class="activity-item"') == 3


def test_widget_states_survive_a_restart(make_client: MakeClient):
    client: TestClient = make_client()
    complete_setup(client)
    widget = Widget(id="inbox", label="Unread", title="Email", icon="", load=None)  # type: ignore[arg-type]

    def note(watch: WidgetWatch, value: int) -> None:
        item = {"key": "gmail-inbox", "widget": widget, "data": WidgetData(value=value)}
        client.portal.call(watch.note, client.app.state.sessionmaker, [item])

    note(WidgetWatch(), 10)
    note(WidgetWatch(), 13)  # a new watch, as after a restart, still knows the 10
    assert "Email · 3 new · unread" in client.get("/activity").text


def test_quiet_day_still_lists_the_latest(make_client: MakeClient):
    from datetime import UTC, datetime, timedelta

    from myorb.activity.models import ActivityEvent

    client: TestClient = make_client()
    complete_setup(client)

    async def add() -> None:
        async with client.app.state.sessionmaker() as db:
            at = datetime.now(UTC) - timedelta(days=2)
            db.add(ActivityEvent(at=at, tone="ok", text="Brief written", url=None))
            await db.commit()

    client.portal.call(add)
    text = client.get("/activity").text
    assert "Brief written" in text
    when = (datetime.now(UTC) - timedelta(days=2)).astimezone(client.app.state.timezone)
    assert f"{when:%-d %b}" in text
