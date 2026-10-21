# Roadmap

This roadmap lists the planned features of Jarvis, grouped into phases. The phases are delivered in order; features within a phase may ship in any order.

## Phase 0: MVP

The smallest useful version for a daily morning routine.

### Platform

- [ ] Web dashboard
- [ ] Responsive layout and PWA, so the dashboard works on a phone
- [ ] Self-hosted deployment as a Docker image (VPS, NAS)
- [x] Built-in login with password and passkeys
- [ ] Google OAuth connection (shared by Gmail, Calendar and later YouTube)
- [ ] Integrations page and setup checklist that guides the user through connecting services
- [ ] Sample data mode with fictional data for development and demos
- [ ] AI powered by Claude (Anthropic API)

### Features

- [ ] **Email stats:** number of unread emails and new emails received today (Gmail)
- [ ] **AI email summary:** summary of important emails and a list of emails waiting for your reply
- [ ] **Calendar:** upcoming events (Google Calendar)
- [ ] **TODO list:** simple task list built into Jarvis
- [ ] **Morning AI brief:** a single "what awaits you today" summary across all sources

## Phase 1: Plugin system

- [ ] Plugin API, so every integration is a plugin
- [ ] Migrate the MVP integrations to plugins
- [ ] Support for custom (private) plugins
- [ ] Plugin developer documentation

## Phase 2: Finances

Built as custom plugins for the author's own invoicing system and wallet app.

- [ ] **Account balances**
- [ ] **Expenses and budget:** monthly spending by category compared to the budget
- [ ] **Invoices:** unpaid issued and received invoices
- [ ] **Investments and crypto:** portfolio value and daily change (from the wallet app)

## Phase 3: Chat and actions

- [ ] **Chat with Jarvis:** ask questions about your data, e.g. "How much did I spend on restaurants this month?"
- [ ] **Actions:** reply to an email (as a Gmail draft), add a TODO, create a calendar event; disabled until enabled in settings, each confirmed in the UI
- [ ] **Triage:** mark items as done or snooze them for later
- [ ] **Command palette:** quick search and commands with ⌘K
- [ ] **Voice control:** talk to Jarvis using speech-to-text and text-to-speech
  - Swappable voice providers, so each speech service is a plugin
  - Speech-to-text: self-hosted Whisper (faster-whisper), so recordings never leave the server
  - Text-to-speech: ElevenLabs for a natural, Jarvis-like voice; Piper as a free, offline fallback

## Phase 4: Work and communication

- [ ] **Slack:** unread messages and mentions
- [ ] **GitHub:** pull requests waiting for your review, your open pull requests, issues and CI status
- [ ] **Jira:** tasks assigned to you across multiple workspaces (e.g. development, support)
- [ ] **Focus time:** free blocks between meetings available for deep work
- [ ] **Time tracking:** built-in time tracker, time worked today and this week (low priority)

## Phase 5: Lifestyle

- [ ] **Weather and commute:** today's forecast and travel time to the first meeting
- [ ] **News:** AI-picked important stories from RSS feeds, Hacker News and similar sources
- [ ] **YouTube:** new videos from channels you are subscribed to (YouTube Data API)
- [ ] **Health:** sleep, steps and activity from Apple Health and Strava
