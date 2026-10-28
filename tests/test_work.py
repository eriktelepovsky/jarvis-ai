from conftest import PASSWORD, MakeClient, complete_setup

from myorb.plugins import Link, Plugin, Row, Stat, Widget, WidgetContext, WidgetData


def service(id: str, name: str, data: WidgetData, position: int) -> Plugin:
    class Service(Plugin):
        def widgets(self) -> list[Widget]:
            async def load(context: WidgetContext) -> WidgetData:
                return data

            return [Widget(id="assigned", label=name, title=name, icon="", load=load)]

    Service.id, Service.name, Service.position = id, name, position
    return Service()


APP = {"group": "acme", "subgroup": "APP"}
JIRA = WidgetData(
    value=2,
    attention=True,
    stats=[Stat(2, "Active")],
    rows=[
        Row("APP-1 Login", "Medium", "In progress", tags=("active", "progress"), **APP),
        Row("APP-2 Old idea", "Low", "Backlog", tags=("backlog",), **APP),
        Row("APP-3 Invoices", "High", "▲ Due 1 Oct", tags=("active", "overdue"), **APP),
    ],
    action=Link("Open Jira", "https://acme.atlassian.net"),
)
ASANA = WidgetData(
    value=1,
    rows=[
        Row(
            "Write the newsletter",
            "Marketing",
            "Today",
            tags=("today",),
            group="Studio",
            subgroup="Newsletter",
        )
    ],
    action=Link("Open Asana", "https://app.asana.com"),
)


def test_jira_and_asana_as_one_number(make_client: MakeClient):
    plugins = {
        "jira": service("jira", "Jira", JIRA, 35),
        "asana": service("asana", "Asana", ASANA, 36),
    }
    client = make_client(plugins=plugins)
    complete_setup(client)
    welcome = client.get("/").text
    assert "/welcome/widget/jira/assigned" in welcome and "/welcome/widget/work/" not in welcome
    assert "/integrations/work" in client.get("/integrations").text

    client.post("/integrations/work", data={"merged": "on"})
    welcome = client.get("/").text
    assert "/welcome/widget/work/assigned" in welcome
    assert "/welcome/widget/jira/assigned" not in welcome
    assert "/welcome/widget/asana/assigned" not in welcome

    work = client.get("/welcome/widget/work/assigned").text
    assert ">3<" in work  # 2 active in Jira and 1 in Asana
    assert "assigned to you · 1 overdue" in work
    # Overdue first, the backlog last; grouped by workspace (with its service), then project.
    order = [work.index(title) for title in ("APP-3", "APP-1", "Write the newsletter", "APP-2")]
    assert order == sorted(order)
    assert 'data-group="Studio · Asana" data-subgroup="Newsletter"' in work
    assert 'data-group="acme · Jira" data-subgroup="APP"' in work
    assert "Open Jira" in work and "Open Asana" in work

    # Kept after a restart, and undone the same way.
    client = make_client(plugins=plugins)
    client.post("/login", data={"password": PASSWORD})
    assert "/welcome/widget/work/assigned" in client.get("/").text
    client.post("/integrations/work", data={"merged": ""})
    assert "/welcome/widget/jira/assigned" in client.get("/").text
