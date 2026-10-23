# Writing plugins

Everything myOrb shows comes from plugins, including the built-in Gmail, Calendar, Jira and Slack integrations. A plugin is a Python package that registers a `Plugin` subclass; it can add:

- **widgets:** numbers around the orb on the welcome screen, with a details dialog behind each,
- **panels:** sections in the side column or under the clock, rendered from your own template,
- **connections:** a form on the Integrations page where the user enters an API token, an address or other values, which myOrb verifies and stores encrypted,
- **facts for the AI brief:** plain-text lines Claude writes the daily brief from.

The [example plugin](../examples/myorb-plugin-example) uses all four. Copy it to start your own.

## The package

```toml
# pyproject.toml
[project]
name = "myorb-wallet"
dependencies = []   # myOrb provides httpx2 and its own API; list only your extras

[project.entry-points."myorb.plugins"]
wallet = "myorb_wallet:WalletPlugin"
```

The entry point is how myOrb finds the plugin. A plugin that fails to load is logged and skipped.

## The plugin

```python
from pathlib import Path

from myorb.plugins import Plugin, Widget, WidgetContext, WidgetData


class WalletPlugin(Plugin):
    id = "wallet"  # unique; also the URL prefix of its templates
    name = "Wallet"
    templates_dir = Path(__file__).parent / "templates"
    position = 70  # order on the welcome screen

    def widgets(self) -> list[Widget]:
        return [
            Widget(
                id="balance",
                label="Balance",
                title="Accounts",
                icon="M3 6h18v13H3z",
                load=self.load,
            )
        ]

    async def load(self, context: WidgetContext) -> WidgetData:
        return WidgetData(value=1234, summary="EUR across 3 accounts")
```

### Widgets

`Widget(refresh=…)` is how often, in seconds, the welcome screen fetches the widget's data again (default 5 minutes). The node's border fills up until then. Set `WidgetData.updated_at` (e.g. `Cached.fetched_at`) so the border starts from the data's real age.

`load(context)` returns `WidgetData`. myOrb renders the number around the orb and the details dialog from it, so all widgets look alike and plugins write no HTML for them:

| Field | Shown as |
|---|---|
| `value` | The number. `None` shows "—" and means there is no data; then set `message`. |
| `summary` | Text next to the big number in the details. |
| `attention` | Amber node and tile: something needs the user. Such a node is drawn large, one with `0` or no value small; the widget's `weight` only adjusts the size a little. |
| `stats` | Up to three `Stat(value, label)` boxes. With `filter="review"`, clicking the box shows only the rows with that tag in `Row(tags=("review",))`. |
| `list_title`, `rows` | A list of `Row(title, subtitle, badge, tone, url, actions)`. `tone` is `ok`, `warn`, `fail`, `info` or `neutral`. `actions` are buttons that POST to a URL. |
| `group_label` | With `Row(group=…)`, e.g. `"workspaces"`: when the rows belong to more than one group, the groups are listed under the numbers to show one of them. `Row(subgroup=…)`, e.g. a Jira project, lists finer groups under each group. |
| `action` | The main button, a `Link(label, url)`. |
| `actions` | Buttons in the footer that POST, e.g. `Action("Mark all as seen", url, {"ids": …})`; the widgets reload afterwards. |
| `links` | More buttons, e.g. one per connected account. `open_buttons("Open Jira", [(name, url), …])` gives one `action` for one account and a button per account for several. |
| `input` | A one-line form (e.g. "Add a task") that POSTs a `text` field. |
| `source`, `updated_at` | "Gmail · 2m ago" under the title. |
| `message`, `message_tone`, `message_action` | A problem or an empty state, with a link to fix it. |
| `label` | Names the number instead of the widget's label, when what it shows varies (e.g. sleep, or kilometres this week without sleep data). |
| `connected` | `False` while the service behind the widget is not set up: the widget is then left out of the orb (the Integrations page offers the setup). Problems with a set-up service keep it visible. |

After a row action or the input, answer with the `HX-Trigger: myorb:refresh` header; the welcome screen reloads its widgets and keeps the dialog open.

### Panels

```python
def panels(self) -> list[Panel]:
    return [Panel(id="today", title="Accounts", template="today.html", load=self.load_panel)]
```

`load` returns the template's context (`context.params` holds the panel request's query parameters, e.g. which day the calendar panel shows); the template is relative to `templates_dir` and autoescaped. `placement="header"` puts it under the clock in the top-right corner instead of the side column; keep it to one short line.

### Connections

Declare a `Provider` with the form fields and `verify()`, and return it from `connections()`:

```python
class WalletApi(Provider):
    id = "wallet"
    name = "Wallet"
    logo = "€"
    summary = "Account balances from your wallet."
    instructions = "connect.html"  # setup steps shown above the form
    fields = [Field("url", "API address", "url"), Field("token", "API token", "password")]

    async def verify(self, http, values) -> Verified:
        data = await request_json(
            http,
            "GET",
            f"{values['url']}/me",
            "The wallet",
            headers={"Authorization": f"Bearer {values['token']}"},
        )
        return Verified(account=data["name"], credentials=values)
```

`verify` raises `ProviderError` with a message for the user when the values do not work; `request_json` already turns HTTP errors into readable ones. myOrb stores `credentials` encrypted, shows the connection on the Integrations page and calls `verify` again for "Test connection".

Read the connected accounts with `context.connections.list("wallet")`, or use `load_connected()` from `myorb.plugins.connection_data`, which loads data per account through the cache and reports problems for the widget.

### Own pages and webhooks

`router()` returns a FastAPI `APIRouter` with the plugin's own pages, e.g. an OAuth callback or a settings page; `settings = SettingsLink(logo, summary, url)` lists that page on the Integrations page. Every route requires a signed-in user, except paths under `/hooks/`, which other services and devices call without a session: authenticate them yourself, e.g. with a secret token in the path compared with `hmac.compare_digest`. The Health plugin uses both, for Strava's OAuth and the Apple Health webhook.

Tables of built-in plugins are created by myOrb's migrations; a custom plugin that needs storage can keep its own SQLite file in the data directory or store small JSON values with `context.connections.settings()`.

### The day's timeline

`timeline(context)` returns today's `TimelineItem(start, end, title)`s, e.g. meetings or a planned run; the report's "Day at a glance" lays them out between morning and evening. The Calendar plugin uses it.

### Background work

`background(context)` returns a coroutine myOrb runs while it is up, e.g. a loop that checks something every minute; `context()` gives a fresh `WidgetContext`. Handle errors inside the loop and sleep between rounds; the task is cancelled at shutdown. The Uptime plugin uses it to check websites.

### Activity

`await record(context.sessionmaker, "Invoice FV-085 was paid", "ok", url)` from `myorb.activity.store` adds a line to the activity list on the welcome screen (tones `ok`, `fail`, `warn`, `info`). myOrb also notes on its own when a widget starts or stops failing and when its number goes up.

### Cache and HTTP

`context.http` is a shared `httpx2.AsyncClient` (10-second timeout). `context.cache.get(key, fetch, fresh, max_age)` returns fresh data immediately, serves stale data while refreshing in the background, and keeps the last good value when a refresh fails. `context.now` is the current time in the user's time zone.

### The AI brief

```python
async def brief(self, context: WidgetContext) -> BriefSection | None:
    return BriefSection(
        "Accounts", ["Business: 18,420.50 EUR", "Restaurants are 24 EUR over budget"]
    )
```

Write facts, most important first; Claude decides what to mention. Return `None` when there is nothing to add.

### Ask

When the user asks a question (a line ending with "?" under the orb), Claude gets the dashboard: every widget's number, stats and rows, as the user sees them in the details. A plugin without widgets contributes its `brief()` facts instead. A plugin whose rows should not go to Claude sets `ask_rows = False`; Claude then gets only its `brief()` facts, as custom sources do.

### More in the details

`WidgetData.details_url` loads an HTML fragment from that URL into the details, under the numbers, e.g. the AI summary of unread emails. Serve it from the plugin's `router()`.

## Developing and testing

Install myOrb from its repository next to your plugin (the example's `pyproject.toml` shows how) and test with `httpx2.MockTransport` instead of the real service. To see the plugin in a running myOrb, install it into myOrb's environment: `uv pip install -e path/to/plugin`.

## Installing

Set `MYORB_PLUGINS` to what pip would install, separated by spaces: package names with versions, wheel URLs or local paths. myOrb installs them at startup into `plugins/` in the data directory, so they survive updates of the Docker image:

```yaml
services:
  myorb:
    environment:
      MYORB_PLUGINS: "myorb-wallet==1.2 /plugins/myorb-invoices"
    volumes:
      - myorb-data:/data
      - ./myorb-invoices:/plugins/myorb-invoices:ro
```

The Docker image has no `git`, so install private plugins from a mounted directory or a wheel rather than a `git+https://` URL.
