import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis, bearer, connections

from myorb.plugins.custom import CustomPlugin

TOKEN = "s3cret-token"

INVOICES = {
    "version": 1,
    "title": "Invoices",
    "label": "Overdue",
    "value": 7,
    "summary": "unpaid · 2 overdue",
    "attention": True,
    "attention_count": 2,
    "stats": [
        {"value": 7, "label": "Unpaid", "filter": "unpaid"},
        {"value": 2, "label": "Overdue", "filter": "overdue"},
        {"value": "4 820 €", "label": "To collect"},
    ],
    "list_title": "Unpaid invoices",
    "group_label": "clients",
    "rows": [
        {
            "title": "2026-041 · Acme <s.r.o.>",
            "subtitle": "1 250,00 €",
            "badge": "▲ Due 15 Oct",
            "tone": "fail",
            "url": "https://invoices.example.com/invoices/41",
            "tags": ["unpaid", "overdue"],
            "group": "Acme",
        },
        {"title": "2026-044 · Beta", "tags": ["unpaid"], "group": "Beta"},
    ],
    "link": {"label": "Open invoices", "url": "https://invoices.example.com/invoices"},
    "brief": ["7 unpaid invoices, 2 overdue."],
}

WALLET = {
    "version": 1,
    "title": "Wallet",
    "value": 3,
    "attention_count": 3,
    "summary": "3 pending",
    "stats": [{"value": 3, "label": "Pending", "filter": "pending"}],
    "rows": [{"title": "Lidl · −23,40 €", "tags": ["pending"]}],
    "link": {"label": "Open wallet", "url": "https://wallet.example.com/"},
}


@pytest.fixture
def apis() -> FakeApis:
    apis = FakeApis()
    apis.routes[("invoices.example.com", "/myorb/")] = bearer(TOKEN, INVOICES)
    apis.routes[("wallet.example.com", "/myorb/")] = bearer(TOKEN, WALLET)
    return apis


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis), plugins={"custom": CustomPlugin()})
    complete_setup(client)
    return client


def add(client: TestClient, name: str, host: str, circle: str = "", token: str = TOKEN):
    data = {"name": name, "url": f"https://{host}/myorb/", "token": token, "circle": circle}
    return client.post("/integrations/custom/connect", data=data)


def test_one_source_is_its_own_circle(client: TestClient):
    assert "/welcome/widget/custom/" not in client.get("/").text
    response = add(client, "Invoices", "invoices.example.com")
    assert "Invoices" in response.text
    assert 'hx-get="/welcome/widget/custom/invoices"' in client.get("/").text

    text = client.get("/welcome/widget/custom/invoices").text
    # The number is what needs the user: the overdue invoices.
    assert '<span class="node-value">2</span>' in text
    assert '<span class="node-label">Invoices</span>' in text  # the circle's name
    assert "overdue · unpaid · 2 overdue" in text  # what the number counts leads the summary
    assert "node warn" in text
    assert 'data-refresh="900"' in text  # every 15 minutes by default
    assert "Acme &lt;s.r.o.&gt;" in text and 'data-tags="unpaid overdue"' in text
    assert 'data-filter="overdue"' in text and 'data-group-value="Acme"' in text
    assert 'href="https://invoices.example.com/invoices"' in text

    [connection] = connections(client)
    client.post(f"/integrations/custom/{connection.id}/disconnect")
    assert "/welcome/widget/custom/" not in client.get("/").text


def test_sources_with_one_circle_are_shown_together(client: TestClient):
    add(client, "Invoices", "invoices.example.com", circle="Finance")
    add(client, "Wallet", "wallet.example.com", circle="Finance")
    page = client.get("/").text
    assert page.count("/welcome/widget/custom/") == 1

    text = client.get("/welcome/widget/custom/finance").text
    assert '<span class="node-value">5</span>' in text  # 2 overdue + 3 pending
    assert '<span class="node-label">Finance</span>' in text
    assert "Invoices: unpaid · 2 overdue · Wallet: 3 pending" in text
    # Each source is a group, its own groups under it; filters stay apart.
    assert 'data-group-value="Invoices" data-subgroup-value="Acme"' in text
    assert 'data-group-value="Wallet"' in text
    assert 'data-filter="s0-overdue"' in text and 'data-filter="s1-pending"' in text
    assert 'data-tags="s1-pending"' in text
    assert "Open invoices" in text and "Open wallet" in text


def test_brief_gets_only_what_the_source_lists(client: TestClient):
    add(client, "Invoices", "invoices.example.com", circle="Finance")
    add(client, "Wallet", "wallet.example.com", circle="Finance")
    context = client.app.state.brief._context
    plugin = client.app.state.plugins["custom"]
    section = client.portal.call(plugin.brief, context())
    assert section.lines == ["7 unpaid invoices, 2 overdue."]


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"title": "X", "value": 1}, "version"),
        ({**WALLET, "rows": [{"title": "x", "url": "javascript:alert(1)"}]}, "rows.0.url"),
        ([1, 2], "answer"),
    ],
)
def test_answers_not_in_the_format_are_explained(
    client: TestClient, apis: FakeApis, answer: object, error: str
):
    apis.routes[("bad.example.com", "/myorb/")] = bearer(TOKEN, answer)
    response = add(client, "Bad", "bad.example.com")
    assert response.status_code == 400
    assert "not in myOrb&#39;s format" in response.text and error in response.text
    assert connections(client) == []


def test_wrong_token_and_plain_http_are_refused(client: TestClient):
    assert "rejected the credentials" in add(client, "W", "wallet.example.com", token="no").text
    data = {"name": "X", "url": "http://wallet.example.com/myorb/", "token": TOKEN}
    response = client.post("/integrations/custom/connect", data=data)
    assert "starting with https://" in response.text
    assert connections(client) == []


def test_a_source_is_edited_in_place(client: TestClient, apis: FakeApis):
    add(client, "Invoices", "invoices.example.com")
    add(client, "Wallet", "wallet.example.com", circle="Finance")
    [invoices, _] = connections(client)
    page = client.get(f"/integrations/custom/{invoices.id}/edit").text
    assert "Edit Invoices" in page and 'value="https://invoices.example.com/myorb/"' in page
    assert TOKEN not in page  # the token stays on the server
    assert "Leave empty to keep the current one" in page

    # Into the Finance circle, every 5 minutes; the token left empty is kept.
    data = {
        "name": "Invoices",
        "url": "https://invoices.example.com/myorb/",
        "token": "",
        "circle": "Finance",
        "refresh": "5",
    }
    response = client.post(f"/integrations/custom/{invoices.id}/edit", data=data)
    assert "Saved and tested again." in response.text
    page = client.get("/").text
    assert page.count("/welcome/widget/custom/") == 1  # one Finance circle now
    assert 'data-refresh="300"' in client.get("/welcome/widget/custom/finance").text
    assert [c.id for c in connections(client)][0] == invoices.id  # the same connection

    taken = client.post(f"/integrations/custom/{invoices.id}/edit", data={**data, "name": "Wallet"})
    assert taken.status_code == 400 and "Wallet is already connected." in taken.text
    assert "+ Add source" in client.get("/integrations").text
