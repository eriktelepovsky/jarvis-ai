from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from sqlalchemy import select

from myorb.tasks.models import Task
from myorb.tasks.parse import parse_task
from myorb.tasks.plugin import TasksPlugin
from myorb.tasks.schedule import due_label, group

TZ = ZoneInfo("Europe/Bratislava")
THURSDAY = date(2026, 10, 22)

# Parsing


@pytest.mark.parametrize(
    ("text", "title", "due_date", "due_time"),
    [
        ("Review PR #318", "Review PR #318", None, None),
        ("zavolať účtovníčke zajtra 10:00", "zavolať účtovníčke", date(2026, 10, 23), time(10)),
        ("kúpiť mlieko dnes", "kúpiť mlieko", THURSDAY, None),
        ("pozajtra odovzdať projekt", "odovzdať projekt", date(2026, 10, 24), None),
        ("Call mom tomorrow at 9am", "Call mom", date(2026, 10, 23), time(9)),
        ("meeting at 3pm", "meeting", THURSDAY, time(15)),
        ("Send VAT documents on Friday", "Send VAT documents", date(2026, 10, 23), None),
        ("nakúpiť v sobotu", "nakúpiť", date(2026, 10, 24), None),
        ("vo štvrtok tréning o 18", "tréning", THURSDAY, time(18)),
        ("stvrtok trening", "trening", THURSDAY, None),
        ("tréning v stredu", "tréning", date(2026, 10, 28), None),
        ("dentist 24.10. o 8:15", "dentist", date(2026, 10, 24), time(8, 15)),
        ("zaplatiť faktúru 5.1.", "zaplatiť faktúru", date(2027, 1, 5), None),
        ("Book tyres 2026-11-03", "Book tyres", date(2026, 11, 3), None),
        ("renew cert 3/11/2026", "renew cert", date(2026, 11, 3), None),
        ("o 10:30 standup", "standup", THURSDAY, time(10, 30)),
        # Not dates or times: stay in the title.
        ("verzia 2.0 release", "verzia 2.0 release", None, None),
        ("enjoy the sun", "enjoy the sun", None, None),
        ("read chapter 31.2", "read chapter 31.2", None, None),
        ("fix bug in ut module", "fix bug in ut module", None, None),
    ],
)
def test_parse_task(text: str, title: str, due_date: date | None, due_time: time | None):
    parsed = parse_task(text, THURSDAY)
    assert (parsed.title, parsed.due_date, parsed.due_time) == (title, due_date, due_time)


# Scheduling


def task(title: str, due: date | None = None, at: time | None = None, created: int = 0) -> Task:
    return Task(
        title=title,
        due_date=due,
        due_time=at,
        created_at=datetime(2026, 10, 1, 0, created, tzinfo=UTC),
    )


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=TZ)


def test_group_by_due_date():
    tasks = [
        task("someday"),
        task("late", date(2026, 10, 20)),
        task("this morning", THURSDAY, time(9)),
        task("tonight", THURSDAY, time(20)),
        task("today", THURSDAY),
        task("friday", date(2026, 10, 23)),
    ]
    groups = group(tasks, NOW)
    assert [t.title for t in groups.overdue] == ["late", "this morning"]
    assert [t.title for t in groups.today] == ["today", "tonight"]
    assert [t.title for t in groups.upcoming] == ["friday"]
    assert [t.title for t in groups.someday] == ["someday"]


@pytest.mark.parametrize(
    ("due", "at", "label"),
    [
        (None, None, ""),
        (date(2026, 10, 19), None, "3d late"),
        (THURSDAY, time(9), "Since 09:00"),
        (THURSDAY, None, "Today"),
        (THURSDAY, time(18), "18:00"),
        (date(2026, 10, 23), None, "Tomorrow"),
        (date(2026, 10, 23), time(9), "Tomorrow 09:00"),
        (date(2026, 10, 26), None, "Mon"),
        (date(2026, 11, 3), time(8, 30), "3 Nov 08:30"),
    ],
)
def test_due_label(due: date | None, at: time | None, label: str):
    assert due_label(task("x", due, at), NOW) == label


# Pages and actions


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    client = make_client(plugins={"tasks": TasksPlugin()})
    complete_setup(client)
    return client


def tasks_in_db(client: TestClient) -> list[Task]:
    async def query() -> list[Task]:
        async with client.app.state.sessionmaker() as db:
            return list(await db.scalars(select(Task).order_by(Task.id)))

    return client.portal.call(query)


def test_add_task_with_due_date(client: TestClient):
    response = client.post("/tasks", data={"text": "Call mom tomorrow at 9am"})
    assert response.url.path == "/tasks"
    [saved] = tasks_in_db(client)
    assert saved.title == "Call mom"
    assert saved.due_time == time(9)
    assert "Call mom" in response.text
    assert "Tomorrow 09:00" in response.text


def test_add_from_welcome_screen_refreshes_widgets(client: TestClient):
    response = client.post("/tasks", data={"text": "Water plants"}, headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "myorb:refresh"


def test_forms_on_the_tasks_page_go_back_to_the_list(client: TestClient):
    # Boosted by htmx: a 204 alone would leave /tasks/<id> (POST only) in the address bar.
    client.post("/tasks", data={"text": "Book tyres"})
    task_id = tasks_in_db(client)[0].id
    boosted = {"HX-Request": "true", "HX-Boosted": "true"}
    data = {"title": "Book winter tyres", "due_date": "", "due_time": ""}
    for path, form in [(f"/tasks/{task_id}", data), (f"/tasks/{task_id}/done", {})]:
        response = client.post(path, data=form, headers=boosted)
        assert response.status_code == 204
        assert response.headers["HX-Location"] == "/tasks"
    assert tasks_in_db(client)[0].title == "Book winter tyres"


def test_empty_task_is_rejected(client: TestClient):
    assert client.post("/tasks", data={"text": "   "}).status_code == 400
    assert tasks_in_db(client) == []


def test_complete_reopen_edit_and_delete(client: TestClient):
    client.post("/tasks", data={"text": "Book tyres"})
    task_id = tasks_in_db(client)[0].id

    page = client.post(f"/tasks/{task_id}/done").text
    assert "Done · last 7 days" in page
    assert tasks_in_db(client)[0].done_at is not None

    client.post(f"/tasks/{task_id}/undo")
    assert tasks_in_db(client)[0].done_at is None

    assert 'name="due_date"' in client.get(f"/tasks?edit={task_id}").text
    data = {"title": "Book winter tyres", "due_date": "2026-11-03", "due_time": "08:30"}
    client.post(f"/tasks/{task_id}", data=data)
    edited = tasks_in_db(client)[0]
    assert (edited.title, edited.due_date, edited.due_time) == (
        "Book winter tyres",
        date(2026, 11, 3),
        time(8, 30),
    )

    client.post(f"/tasks/{task_id}/delete")
    assert tasks_in_db(client) == []
    assert client.post(f"/tasks/{task_id}/done").status_code == 404


def test_invalid_edit_is_rejected(client: TestClient):
    client.post("/tasks", data={"text": "Book tyres"})
    task_id = tasks_in_db(client)[0].id
    response = client.post(f"/tasks/{task_id}", data={"title": "x", "due_date": "31.2."})
    assert response.status_code == 400


def test_tasks_require_sign_in(client: TestClient):
    client.cookies.clear()
    assert client.get("/tasks", follow_redirects=False).status_code == 303
    assert client.post("/tasks", data={"text": "x"}).status_code == 401


# Widget and panel


def test_widget_shows_open_tasks_with_actions(client: TestClient):
    client.post("/tasks", data={"text": "Old chore 1.1.2020"})
    client.post("/tasks", data={"text": "Water plants"})
    text = client.get("/welcome/widgets").text
    assert '<span class="node-value">2</span>' in text
    assert "open · 1 overdue" in text
    assert "node warn" in text
    assert 'hx-post="/tasks/1/done"' in text
    assert 'hx-post="/tasks"' in text
    assert "All tasks" in text
    assert '<span class="node-label">Tasks</span>' in text
    assert '<a class="row-btn" href="/tasks?edit=1">Edit</a>' in text


def test_widget_can_undo_tasks_done_today(client: TestClient):
    client.post("/tasks", data={"text": "Water plants"})
    client.post("/tasks/1/done")
    text = client.get("/welcome/widgets").text
    assert "✓ Done" in text
    assert 'hx-post="/tasks/1/undo"' in text
    client.post("/tasks/1/undo")
    assert 'hx-post="/tasks/1/done"' in client.get("/welcome/widgets").text


def test_panel_lists_overdue_today_and_undated(client: TestClient):
    assert "Nothing due today." in client.get("/panels/tasks/today").text
    client.post("/tasks", data={"text": "Next year 1.1.2099"})
    assert "Nothing due today. 1 more task later." in client.get("/panels/tasks/today").text
    client.post("/tasks", data={"text": "Old chore 1.1.2020"})
    client.post("/tasks", data={"text": "Water plants today"})
    client.post("/tasks", data={"text": "Someday maybe"})
    text = client.get("/panels/tasks/today").text
    # Without a date, a task waits here after today's; one due later does not.
    assert text.index("Old chore") < text.index("Water plants") < text.index("Someday maybe")
    assert "No due date" in text and "Next year" not in text
    assert 'class="t2 overdue"' in text


def test_command_line_adds_a_task(client: TestClient):
    assert 'id="command-input"' in client.get("/").text
    response = client.post("/command", data={"text": "Call <the> accountant tomorrow 10:00"})
    assert response.headers["HX-Trigger"] == "myorb:refresh"
    assert "✓ Added to Tasks" in response.text
    assert "Call &lt;the&gt; accountant</span> · Tomorrow 10:00" in response.text
    assert "Call &lt;the&gt; accountant" in client.get("/tasks").text
    assert client.post("/command", data={"text": "  "}).status_code == 400


def test_tasks_are_not_in_the_menu(client: TestClient):
    page = client.get("/").text
    assert '<span class="label strong">Tasks</span>' in page  # the side panel
    assert 'class="label" href="/tasks"' not in page
    assert "<h1>Tasks</h1>" in client.get("/tasks").text


def test_repeats_are_recognized_in_the_text():
    tuesday = date(2026, 10, 27)
    cases = {
        "vyniesť smeti každý pondelok": ("vyniesť smeti", date(2026, 11, 2), "week:1"),
        "zaplatiť nájom každý mesiac 5.": ("zaplatiť nájom", date(2026, 11, 5), "month:1:5"),
        "pay rent every month on the 1st": ("pay rent", date(2026, 11, 1), "month:1:1"),
        "standup every weekday 9:00": ("standup", tuesday, "weekdays"),
        "water plants every 3 days": ("water plants", tuesday, "day:3"),
        "backup every 2 weeks from Friday": ("backup", date(2026, 10, 30), "week:2"),
        "denne cvičiť": ("cvičiť", tuesday, "day:1"),
        "verzia 2.0 release": ("verzia 2.0 release", None, None),
    }
    for text, (title, due, rule) in cases.items():
        parsed = parse_task(text, tuesday)
        assert (parsed.title, parsed.due_date, parsed.repeat) == (title, due, rule), text


def test_next_due_counts_from_the_due_date():
    from myorb.tasks.repeat import describe, next_due

    monday = date(2026, 11, 2)
    # Done on Wednesday after, the next one is still a Monday; an occurrence missed is skipped.
    assert next_due("week:1", monday, date(2026, 11, 4)) == date(2026, 11, 9)
    assert next_due("day:1", monday, date(2026, 11, 5)) == date(2026, 11, 6)
    assert next_due("weekdays", date(2026, 10, 30), date(2026, 10, 30)) == monday
    # The 31st in a month without one is its last day, and the 31st again after.
    assert next_due("month:1:31", date(2026, 1, 31), date(2026, 1, 31)) == date(2026, 2, 28)
    assert next_due("month:1:31", date(2026, 2, 28), date(2026, 2, 28)) == date(2026, 3, 31)
    assert next_due("year", date(2028, 2, 29), date(2028, 2, 29)) == date(2029, 2, 28)
    assert describe("week:1", monday) == "every Monday"
    assert describe("month:1:5") == "every month on the 5th"
    assert describe("week:2", monday) == "every 2 weeks on Monday"


def test_a_repeating_task_moves_on_when_done(client: TestClient):
    client.post("/tasks", data={"text": "Water plants every 3 days"})
    page = client.get("/tasks").text
    assert "Water plants" in page and "↻ every 3 days" in page
    client.post("/tasks/1/done")
    page = client.get("/tasks").text
    # Still open, due three days later, with nothing in Done.
    assert "Water plants" in page and "Done · last 7 days" not in page
    assert "Upcoming" in page and "Today" not in page.split("Upcoming")[0].split("Tasks</h1>")[-1]
    edit = client.get("/tasks?edit=1").text
    assert '<option value="day:3" selected>Every 3 days</option>' in edit
    assert "next" in client.get("/activity").text
    client.post("/tasks/1/undo")
    assert "Today" in client.get("/panels/tasks/today").text

    # Changed in the edit form: no longer repeats, so done is done.
    client.post("/tasks/1", data={"title": "Water plants", "due_date": "", "repeat": "none"})
    assert "↻" not in client.get("/tasks").text
    client.post("/tasks/1/done")
    assert "Done · last 7 days" in client.get("/tasks").text
    assert client.post("/tasks/1", data={"title": "x", "repeat": "nonsense"}).status_code == 400
