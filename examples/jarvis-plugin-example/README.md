# Jarvis plugin example

A complete, small Jarvis plugin to copy when you write your own: account balances from a JSON API. It shows every part of the plugin API — a connection set up with a form, a number around the orb with details, a side panel and facts for the AI brief. See [writing plugins](../../docs/PLUGINS.md).

```sh
cd examples/jarvis-plugin-example
uv sync            # installs Jarvis from this repository, editable
uv run pytest
```

To try it in a running Jarvis, install it into Jarvis's environment (`uv pip install -e examples/jarvis-plugin-example` from the repository root) or list it in `JARVIS_PLUGINS`.
