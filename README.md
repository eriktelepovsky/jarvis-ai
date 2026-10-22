# Jarvis

Jarvis is your personal AI-powered daily briefing dashboard. It connects to email, calendar, tasks, finances and other third-party services, then uses AI to summarize what matters today: unread and new emails, upcoming events, open TODOs, spending and more. Self-hosted, extensible with plugins and written in Python.

> 🚧 Work in progress.

## Run with Docker

```sh
curl -O https://raw.githubusercontent.com/eriktelepovsky/jarvis-hud/main/compose.yaml
docker compose up -d
docker logs jarvis
```

The log contains a setup link (`/setup?token=…`). Open it at http://your-server:8000 and choose a password. Data is stored in the `jarvis-data` volume.

When Jarvis runs behind a reverse proxy with HTTPS, set `JARVIS_PUBLIC_URL` (e.g. `https://jarvis.example.com`) and `JARVIS_TRUSTED_PROXIES` (the IP address or network of the proxy) in `compose.yaml` or a `.env` file next to it. Passkeys require HTTPS, except on `localhost`.

Connected accounts are stored encrypted with a key that Jarvis generates in the data volume. To keep the key separate from the data, set `JARVIS_SECRET_KEY` to a long random value (e.g. `openssl rand -hex 32`) and keep it; changing or losing it means connecting all accounts again.

## Development

```sh
uv sync
uv run jarvis serve --reload
uv run pytest
```

Jarvis runs at http://127.0.0.1:8000. On the first visit, Jarvis asks you to set a password. See the [architecture](docs/ARCHITECTURE.md) for how Jarvis is built.

### Local domain

To open Jarvis at http://jarvis.localhost instead, run it on port 80 with `uv run jarvis serve --host 0.0.0.0 --port 80`, or put `JARVIS_HOST=0.0.0.0` and `JARVIS_PORT=80` in a `.env` file. macOS allows port 80 without `sudo` only on all network interfaces, so Jarvis is then also reachable from your local network, protected by the login. Addresses ending in `.localhost` point to your own computer in macOS and modern browsers, so no hosts entry is needed. Passkeys only work at the address where they were added, so always use the same one.

## Roadmap

See the [roadmap](ROADMAP.md) for planned features and their order.

## Contributing

Contributions are welcome! Before your first pull request can be merged, you need to sign the [Contributor License Agreement](CLA.md). The CLA bot will guide you through it with a single comment in your pull request.

## License

Copyright © 2026 Erik Telepovský

Jarvis is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of Jarvis as a network service, you must make its source code available to its users.

For commercial licensing that is not subject to the AGPL terms, please contact the author.
