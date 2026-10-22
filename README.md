# Jarvis

Jarvis is your personal AI-powered daily briefing dashboard. It connects to email, calendar, tasks, finances and other third-party services, then uses AI to summarize what matters today: unread and new emails, upcoming events, open TODOs, spending and more. Self-hosted, extensible with plugins and written in Python.

> 🚧 Work in progress.

## Run on your computer

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python by itself).

```sh
git clone https://github.com/eriktelepovsky/jarvis-hud.git
cd jarvis-hud
uv sync
uv run jarvis serve
```

Open http://localhost:8000 and choose a password. Data (the SQLite database and the encryption key) is stored in `./data`.

Settings come from `JARVIS_*` environment variables or a `.env` file in the project directory, e.g.:

```sh
JARVIS_TIMEZONE=Europe/Bratislava
```

### Local domain

To open Jarvis at http://jarvis.localhost instead, run it on port 80 with `uv run jarvis serve --host 0.0.0.0 --port 80`, or put `JARVIS_HOST=0.0.0.0` and `JARVIS_PORT=80` in `.env`. macOS allows port 80 without `sudo` only on all network interfaces, so Jarvis is then also reachable from your local network, protected by the login. Addresses ending in `.localhost` point to your own computer in macOS and modern browsers, so no hosts entry is needed. Passkeys only work at the address where they were added, so always use the same one.

Google accepts sign-in redirects only on `localhost` or over HTTPS, so connect Google while Jarvis is open at http://localhost (see Integrations → Google).

## Run with Docker

The image is `ghcr.io/eriktelepovsky/jarvis-hud`, built for `amd64` and `arm64` and published with every release.

### Docker Compose

```sh
curl -O https://raw.githubusercontent.com/eriktelepovsky/jarvis-hud/main/compose.yaml
echo "JARVIS_TIMEZONE=Europe/Bratislava" > .env
docker compose up -d
docker logs jarvis
```

The log contains a setup link (`/setup?token=…`). Open it at http://your-server:8000 and choose a password. Requests reach the container from the Docker network, so setup always needs this link. Data is stored in the `jarvis-data` volume.

### Plain Docker

```sh
docker run -d --name jarvis --restart unless-stopped \
  -p 8000:8000 -v jarvis-data:/data \
  -e JARVIS_TIMEZONE=Europe/Bratislava \
  ghcr.io/eriktelepovsky/jarvis-hud:latest
docker logs jarvis
```

### Build the image yourself

Until the first release is published, or to run your own changes, build the image from the source and use it with the commands above:

```sh
docker build -t ghcr.io/eriktelepovsky/jarvis-hud:latest .
docker compose up -d
```

## Run in production

In production, Jarvis runs behind a reverse proxy that provides HTTPS on your domain. HTTPS is required for passkeys, for connecting Google and for installing Jarvis as an app on a phone.

This example uses [Caddy](https://caddyserver.com/), which gets a certificate from Let's Encrypt automatically. Point your domain's DNS record to the server, then put these two files in one directory:

`compose.yaml`:

```yaml
services:
  jarvis:
    image: ghcr.io/eriktelepovsky/jarvis-hud:latest
    container_name: jarvis
    restart: unless-stopped
    volumes:
      - jarvis-data:/data
    environment:
      JARVIS_TIMEZONE: Europe/Bratislava
      JARVIS_PUBLIC_URL: https://jarvis.example.com
      # Caddy reaches Jarvis from the Docker network (in 172.16.0.0/12 by default);
      # trust its X-Forwarded-* headers.
      JARVIS_TRUSTED_PROXIES: 172.16.0.0/12
      JARVIS_SECRET_KEY: ${JARVIS_SECRET_KEY}

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data

volumes:
  jarvis-data:
  caddy-data:
```

`Caddyfile`:

```
jarvis.example.com {
    reverse_proxy jarvis:8000
}
```

Generate the encryption key once and keep it safe, e.g. in a password manager:

```sh
echo "JARVIS_SECRET_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d
docker logs jarvis
```

Open the setup link from the log at `https://jarvis.example.com/setup?token=…`. Jarvis is not exposed directly: only Caddy publishes ports.

Then, in Jarvis:

- **Google:** in your OAuth client in Google Cloud Console, add the redirect URI `https://jarvis.example.com/integrations/google/callback`. You can keep using the same client as for local development.
- **Claude:** add your Anthropic API key in Integrations → Claude.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `JARVIS_TIMEZONE` | system time zone | Your time zone, e.g. `Europe/Bratislava`. Containers run in UTC, so set it. |
| `JARVIS_PUBLIC_URL` | address of each request | Public HTTPS address. Used for passkeys, the CSRF check, `Secure` cookies and the Google redirect URI. |
| `JARVIS_TRUSTED_PROXIES` | `127.0.0.1` | IP addresses or networks of the reverse proxy whose `X-Forwarded-For` and `X-Forwarded-Proto` headers are trusted, or `*`. |
| `JARVIS_SECRET_KEY` | generated in the data directory | Key that encrypts connected accounts and API keys. Keep it outside the data volume and do not lose it: changing it means connecting all accounts again. |
| `JARVIS_DATA_DIR` | `data` (`/data` in Docker) | Where the database and the generated key are stored. |
| `JARVIS_HOST`, `JARVIS_PORT` | `127.0.0.1`, `8000` (`0.0.0.0` in Docker) | Interface and port to listen on. |

### Updates and backups

Update to the latest release:

```sh
docker compose pull
docker compose up -d
```

Database migrations run automatically at startup.

Back up the database while Jarvis runs (SQLite's backup keeps it consistent), then copy it out of the container:

```sh
docker exec jarvis python -c "import sqlite3; sqlite3.connect('/data/jarvis.db').backup(sqlite3.connect('/data/backup.db'))"
docker cp jarvis:/data/backup.db ./jarvis-backup.db
```

Without `JARVIS_SECRET_KEY`, also back up `/data/secret.key`; the connected accounts in the backup cannot be read without it.

## Development

```sh
uv sync
uv run jarvis serve --reload   # restarts on code changes
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

See the [architecture](docs/ARCHITECTURE.md) for how Jarvis is built.

## Roadmap

See the [roadmap](ROADMAP.md) for planned features and their order.

## Contributing

Contributions are welcome! Before your first pull request can be merged, you need to sign the [Contributor License Agreement](CLA.md). The CLA bot will guide you through it with a single comment in your pull request.

## License

Copyright © 2026 Erik Telepovský

Jarvis is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of Jarvis as a network service, you must make its source code available to its users.

For commercial licensing that is not subject to the AGPL terms, please contact the author.
