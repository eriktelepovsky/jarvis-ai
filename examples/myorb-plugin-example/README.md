# myOrb plugin example

A complete, small myOrb plugin to copy when you write your own: account balances from a JSON API. It shows every part of the plugin API — a connection set up with a form, a number around the orb with details, a side panel and facts for the AI brief. See [writing plugins](../../docs/PLUGINS.md).

```sh
cd examples/myorb-plugin-example
uv sync            # installs myOrb from this repository, editable
uv run pytest
```

To try it in a running myOrb, install it into myOrb's environment (`uv pip install -e examples/myorb-plugin-example` from the repository root) or list it in `MYORB_PLUGINS`.
