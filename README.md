# Jarvis

Jarvis is your personal AI-powered daily briefing dashboard. It connects to email, calendar, tasks, finances and other third-party services, then uses AI to summarize what matters today: unread and new emails, upcoming events, open TODOs, spending and more. Self-hosted, extensible with plugins and written in Python.

> 🚧 Work in progress.

## Development

```sh
uv sync
uv run jarvis serve --reload
uv run pytest
```

The dashboard runs at http://127.0.0.1:8000. On the first visit, Jarvis asks you to set a password. See the [architecture](docs/ARCHITECTURE.md) for how Jarvis is built.

## Roadmap

See the [roadmap](ROADMAP.md) for planned features and their order.

## Contributing

Contributions are welcome! Before your first pull request can be merged, you need to sign the [Contributor License Agreement](CLA.md). The CLA bot will guide you through it with a single comment in your pull request.

## License

Copyright © 2026 Erik Telepovský

Jarvis is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of Jarvis as a network service, you must make its source code available to its users.

For commercial licensing that is not subject to the AGPL terms, please contact the author.
