# myOrb

myOrb is your personal AI-powered daily briefing dashboard. It connects to email, calendar, tasks and other third-party services, then uses AI to summarize what matters today: unread and new emails, upcoming events, open TODOs and more. Your own apps can join in as custom sources, e.g. your invoicing or a wallet app to keep an eye on your finances. Self-hosted, extensible with plugins and written in Python.

> 🚧 Work in progress.

![myOrb welcome screen: the orb with numbers from email, calendar, tasks, pull requests, Jira, finance, Slack, uptime, sleep and YouTube; today's schedule, tasks, priorities from the brief and the weather on the left; quick actions, recent activity and a YouTube player on the right](https://raw.githubusercontent.com/eriktelepovsky/myorb/main/docs/images/welcome.jpg)

*The welcome screen, with sample data.*

Your assistant is yours to name: call it J.A.R.V.I.S., Iris, HAL 9000 or anything else at setup, and it greets you, sits in the middle of the orb and writes your brief under that name.

## Features

- **The orb:** one number per source around your assistant, larger when something needs you. Click a number for its details: the list behind it, filters and actions; read an email right there and mark it as read.
- **Sources:** Gmail, Google Calendar, the built-in task list, pull requests from GitHub, GitLab and Bitbucket, Jira, Asana, Slack mentions and unread direct messages, uptime checks of your websites (every minute, with TLS certificate warnings), weather from Open-Meteo, YouTube subscriptions, Apple Health and Strava, and your own apps through [custom sources](https://github.com/eriktelepovsky/myorb/blob/main/docs/CUSTOM_SOURCES.md), e.g. invoices and spending from your invoicing or wallet app.
- **Daily brief:** Claude writes what awaits you today at a time you choose, in English or Slovak, with what needs you first. Read it aloud with your browser's voices or Microsoft Azure Speech, in several languages.
- **Ask:** ask about your dashboard in the command line, by voice, or with one of the quick actions. Claude can also act for you once you confirm: add tasks, draft email replies in Gmail, add and change calendar events, mark videos as seen, add uptime monitors, change Jira issues, complete Asana tasks, approve or comment on pull requests. Most actions can be undone. Asked to, it loads new data from every source. Issues, tasks, pull requests and emails it names link to them, and notifications from Sentry and Bitbucket Pipelines straight to the issue or pipeline.
- **Focus time:** the free blocks of an hour or more between today's meetings, in the schedule, the calendar's details and the brief. Ask can block one in your calendar.
- **Commute:** when to leave for today's events that happen somewhere, with the traffic expected then (Google Routes API): in the header, the schedule and the brief.
- **Tasks:** due dates written in plain language, e.g. “call Anna tomorrow 10:00”.
- **Side columns:** today's schedule, tasks, priorities from the brief and the weather on the left; quick actions and what happened lately on the right.
- **Music and sounds:** generated ambient music or any YouTube video or playlist while you work, and quiet interface sounds.
- **Yours:** self-hosted, a password and passkeys for sign-in, works on a phone and installs as an app. Claude's cost is counted in euros. Extend it with [plugins](https://github.com/eriktelepovsky/myorb/blob/main/docs/PLUGINS.md).

![The daily brief: the day at a glance, a summary, what needs you and every source with its change since yesterday](https://raw.githubusercontent.com/eriktelepovsky/myorb/main/docs/images/report.jpg)

*The daily brief.*

![Details of a number: Jira issues assigned to you, grouped by workspace](https://raw.githubusercontent.com/eriktelepovsky/myorb/main/docs/images/details.jpg)

*Details behind a number.*

![Ask: Claude plans the morning from the dashboard and offers to add a task](https://raw.githubusercontent.com/eriktelepovsky/myorb/main/docs/images/ask.jpg)

*Ask, with an action to confirm.*

<img src="https://raw.githubusercontent.com/eriktelepovsky/myorb/main/docs/images/phone.jpg" alt="myOrb on a phone: the orb with its numbers and the command line" width="300">

*On a phone.*

## Run on your computer

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python by itself).

```sh
git clone https://github.com/eriktelepovsky/myorb.git
cd myorb
uv sync
uv run myorb serve
```

Open http://localhost:8000 and choose a password. Data (the SQLite database and the encryption key) is stored in `./data`.

Settings come from `MYORB_*` environment variables or a `.env` file in the project directory, e.g.:

```sh
MYORB_TIMEZONE=Europe/Bratislava
```

### Local domain

To open myOrb at http://myorb.localhost instead, run it on port 80 with `uv run myorb serve --host 0.0.0.0 --port 80`, or put `MYORB_HOST=0.0.0.0` and `MYORB_PORT=80` in `.env`. macOS allows port 80 without `sudo` only on all network interfaces, so myOrb is then also reachable from your local network, protected by the login. Addresses ending in `.localhost` point to your own computer in macOS and modern browsers, so no hosts entry is needed. Passkeys only work at the address where they were added, so always use the same one.

Google accepts sign-in redirects only on `localhost` or over HTTPS, so connect Google while myOrb is open at http://localhost (see Integrations → Google).

## Run with Docker

The image is `ghcr.io/eriktelepovsky/myorb`, built for `amd64` and `arm64` and published with every release.

### Docker Compose

```sh
curl -O https://raw.githubusercontent.com/eriktelepovsky/myorb/main/compose.yaml
echo "MYORB_TIMEZONE=Europe/Bratislava" > .env
docker compose up -d
docker logs myorb
```

The log contains a setup link (`/setup?token=…`). Open it at http://your-server:8000 and choose a password. Requests reach the container from the Docker network, so setup always needs this link. Data is stored in the `myorb-data` volume.

### Plain Docker

```sh
docker run -d --name myorb --restart unless-stopped \
  -p 8000:8000 -v myorb-data:/data \
  -e MYORB_TIMEZONE=Europe/Bratislava \
  ghcr.io/eriktelepovsky/myorb:latest
docker logs myorb
```

### Build the image yourself

Until the first release is published, or to run your own changes, build the image from the source and use it with the commands above:

```sh
docker build -t ghcr.io/eriktelepovsky/myorb:latest .
docker compose up -d
```

## Run in production

In production, myOrb runs behind a reverse proxy that provides HTTPS on your domain. HTTPS is required for passkeys, for connecting Google and for installing myOrb as an app on a phone.

This example uses [Caddy](https://caddyserver.com/), which gets a certificate from Let's Encrypt automatically. Point your domain's DNS record to the server, then put these two files in one directory:

`compose.yaml`:

```yaml
services:
  myorb:
    image: ghcr.io/eriktelepovsky/myorb:latest
    container_name: myorb
    restart: unless-stopped
    volumes:
      - myorb-data:/data
    environment:
      MYORB_TIMEZONE: Europe/Bratislava
      MYORB_PUBLIC_URL: https://myorb.example.com
      # Caddy reaches myOrb from the Docker network (in 172.16.0.0/12 by default);
      # trust its X-Forwarded-* headers.
      MYORB_TRUSTED_PROXIES: 172.16.0.0/12
      MYORB_SECRET_KEY: ${MYORB_SECRET_KEY}

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
  myorb-data:
  caddy-data:
```

`Caddyfile`:

```
myorb.example.com {
    reverse_proxy myorb:8000
}
```

Generate the encryption key once and keep it safe, e.g. in a password manager:

```sh
echo "MYORB_SECRET_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d
docker logs myorb
```

Open the setup link from the log at `https://myorb.example.com/setup?token=…`. myOrb is not exposed directly: only Caddy publishes ports.

Then, in myOrb:

- **Google:** in your OAuth client in Google Cloud Console, add the redirect URI `https://myorb.example.com/integrations/google/callback`. You can keep using the same client as for local development.
- **Claude:** add your Anthropic API key in Integrations → Claude.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `MYORB_TIMEZONE` | system time zone | Your time zone, e.g. `Europe/Bratislava`. Containers run in UTC, so set it. |
| `MYORB_PUBLIC_URL` | address of each request | Public HTTPS address. Used for passkeys, the CSRF check, `Secure` cookies and the Google redirect URI. |
| `MYORB_TRUSTED_PROXIES` | `127.0.0.1` | IP addresses or networks of the reverse proxy whose `X-Forwarded-For` and `X-Forwarded-Proto` headers are trusted, or `*`. |
| `MYORB_SECRET_KEY` | generated in the data directory | Key that encrypts connected accounts and API keys. Keep it outside the data volume and do not lose it: changing it means connecting all accounts again. |
| `MYORB_DATA_DIR` | `data` (`/data` in Docker) | Where the database and the generated key are stored. |
| `MYORB_PLUGINS` | none | Custom plugins to install at startup, as for pip: `myorb-wallet==1.2`, a wheel URL or a mounted directory. See [writing plugins](https://github.com/eriktelepovsky/myorb/blob/main/docs/PLUGINS.md). |
| `MYORB_HOST`, `MYORB_PORT` | `127.0.0.1`, `8000` (`0.0.0.0` in Docker) | Interface and port to listen on. |

### Updates and backups

Update to the latest release:

```sh
docker compose pull
docker compose up -d
```

Database migrations run automatically at startup.

Back up the database while myOrb runs (SQLite's backup keeps it consistent), then copy it out of the container:

```sh
docker exec myorb python -c "import sqlite3; sqlite3.connect('/data/myorb.db').backup(sqlite3.connect('/data/backup.db'))"
docker cp myorb:/data/backup.db ./myorb-backup.db
```

Without `MYORB_SECRET_KEY`, also back up `/data/secret.key`; the connected accounts in the backup cannot be read without it.

## Development

```sh
uv sync
uv run myorb serve --reload   # restarts on code changes
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

See the [architecture](https://github.com/eriktelepovsky/myorb/blob/main/docs/ARCHITECTURE.md) for how myOrb is built, and [writing plugins](https://github.com/eriktelepovsky/myorb/blob/main/docs/PLUGINS.md) to connect your own services.

## Roadmap

See the [roadmap](https://github.com/eriktelepovsky/myorb/blob/main/ROADMAP.md) for planned features and their order.

## Contributing

Contributions are welcome! Before your first pull request can be merged, you need to sign the [Contributor License Agreement](https://github.com/eriktelepovsky/myorb/blob/main/CLA.md). The CLA bot will guide you through it with a single comment in your pull request.

## License

Copyright © 2026 Erik Telepovský

myOrb is licensed under the [GNU Affero General Public License v3.0](https://github.com/eriktelepovsky/myorb/blob/main/LICENSE) (AGPL-3.0). If you run a modified version of myOrb as a network service, you must make its source code available to its users.

For commercial licensing that is not subject to the AGPL terms, please contact the author.
