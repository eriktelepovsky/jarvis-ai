# Roadmap

This roadmap lists the planned features of myOrb, grouped into phases. The phases are delivered in order; features within a phase may ship in any order.

## Phase 0: MVP

The smallest useful version for a daily morning routine.

### Platform

- [x] Welcome screen: orb with numbers and details, today's schedule, clock, ambient music and interface sounds
- [x] Responsive layout and PWA, so myOrb works on a phone
- [x] Self-hosted deployment as a Docker image (VPS, NAS)
- [x] Built-in login with password and passkeys
- [x] Google OAuth connection (shared by Gmail, Calendar and later YouTube)
- [x] Integrations page and setup checklist that guides the user through connecting services
- [x] Sample data mode with fictional data for development and demos (`MYORB_DEMO=1`)
- [x] AI powered by Claude (Anthropic API)

### Features

- [x] **Email stats:** number of unread emails and new emails received today (Gmail)
- [x] **AI email summary:** in the email details, what needs you (e.g. waiting for your reply), what is worth a look and how many can wait
- [x] **Calendar:** upcoming events (Google Calendar)
- [x] **TODO list:** simple task list built into myOrb, with due dates written in plain language
- [x] **Morning AI brief:** a single "what awaits you today" summary across all sources

## Phase 1: Plugin system

- [x] Plugin API, so every integration is a plugin (widgets, panels, connections, brief facts)
- [x] Migrate the MVP integrations to plugins
- [x] Support for custom (private) plugins, installed with `MYORB_PLUGINS`
- [x] Plugin developer documentation and an example plugin

## Phase 2: Finances

Built as custom plugins for the author's own invoicing system and wallet app.

- [ ] **Account balances**
- [ ] **Expenses and budget:** monthly spending by category compared to the budget
- [ ] **Invoices:** unpaid issued and received invoices
- [ ] **Investments and crypto:** portfolio value and daily change (from the wallet app)

## Phase 3: Chat and actions

- [x] **Chat with myOrb:** everything typed under the orb goes to the chat about your dashboard, with follow-ups
- [x] **Actions in chat:** Claude proposes, you confirm: tasks, YouTube videos seen, uptime monitors, Jira status and comments, Asana tasks, approving and commenting GitHub pull requests; most can be undone
- [x] **Reply to an email as a Gmail draft:** written by Claude on the email's page or in the chat, never sent by myOrb
- [x] **Calendar in chat:** add, move, rename and delete events, answer invitations (Google permission to change events)
- [x] **Email:** read an email and its conversation in myOrb (images blocked until asked for) and mark it as read
- [x] **Quick actions:** common questions for Ask (plan today, inbox brief, focus block…) one click away
- [ ] **Triage:** mark items as done or snooze them for later
- [ ] **Command palette:** quick search and commands with ⌘K
- [ ] **Voice control:** talk to myOrb using speech-to-text and text-to-speech
  - Swappable voice providers, so each speech service is a plugin
  - Speech-to-text: self-hosted Whisper (faster-whisper), so recordings never leave the server
  - Text-to-speech: ElevenLabs for a natural, myOrb-like voice; Piper as a free, offline fallback

## Phase 4: Work and communication

- [x] **Slack:** mentions from the last 24 hours, across workspaces
- [x] **Slack:** unread direct and group messages
- [x] **Pull requests (GitHub, GitLab, Bitbucket):** waiting for your review and your own, with CI status
- [x] **Jira:** tasks assigned to you across multiple workspaces (e.g. development, support)
- [x] **Asana:** tasks assigned to you across your workspaces, overdue and due soon first
- [x] **Custom sources:** your own apps (e.g. invoicing, a wallet) answering with JSON in myOrb's format, alone or together as one circle (see [docs/CUSTOM_SOURCES.md](docs/CUSTOM_SOURCES.md))
- [x] **Uptime:** myOrb checks your websites every minute, records outages and warns before TLS certificates expire
- [ ] **Uptime:** import monitors from Uptime Kuma, UptimeRobot or Better Stack
- [x] **Focus time:** free blocks between meetings available for deep work
- [ ] **Time tracking:** built-in time tracker, time worked today and this week (low priority)

## Phase 5: Lifestyle

- [x] **Weather:** today's forecast and rain by the hour (Open-Meteo)
- [x] **Commute:** travel time to the first meeting
- [ ] **News:** AI-picked important stories from RSS feeds, Hacker News and similar sources
- [x] **YouTube:** new videos from channels you are subscribed to (YouTube Data API)
- [x] **Health:** sleep and steps from Apple Health (iPhone Shortcut and webhook), activities from Strava
