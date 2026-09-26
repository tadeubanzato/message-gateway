# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/), and this
project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - Unreleased

### Added
- **Guided browser setup** at `http://localhost:8010`: choose local SQLite or
  MongoDB Atlas, then create the administrator account. No `.env` file needed.
- `install.sh` one-command installer (checks Docker, builds, waits for healthy,
  opens the setup page in the default browser) and a rewritten `llms.txt` so a
  single prompt installs everything.
- **Credentials stored encrypted in the database** (Fernet). The encryption key
  and token-hashing secret are generated automatically on first start.
- **Multiple providers per channel.** Connect several email/SMS/push providers,
  pick a default per channel, and choose one per message with `"provider"`.
  `GET /v1/providers` lists what is available.
- **Full message log** in the chosen database: time, channel, recipient,
  subject and body (encrypted), status, provider, attempts and errors. API
  (`GET /v1/messages`, `GET /v1/messages/{id}`) and portal pages with search.
- **Redesigned portal** with a navbar: Home, Channels (one page per channel),
  Message log, API keys, About, and Account / Settings under the user menu.
  Shared stylesheet with light and dark themes.
- Administrator role: the first account can change settings and channels; sign-ups
  are closed by default and secret changes require the administrator's password.

- **Pushover applications**: add any number of named apps, each with its own token,
  in the web app, plus a default app. Sends and MCP calls can name an `app`.
- **Custom SMS endpoint** (replaces the fixed "local modem" provider): set your own URL,
  headers and JSON body pattern with `{{to}}`, `{{message}}` placeholders and a live preview.
- **Default SMS phone number**, used when a message names no recipient.
- **Import from `.env`** in Settings for migrating older installs.
- Sign-in and API-key links (open in a new tab) on every provider card.
- MCP: `send_push`, `send_email`, `send_sms` and `send_test` tools that wait for and report the
  delivery result; free-form phone numbers are normalized (country code required).

### Changed
- `.env` is now optional and only for advanced overrides; every variable in
  `.env.example` is commented out. Variables set in `.env` override the browser
  settings.
- The gateway port is bound to `127.0.0.1` by default. `MG_PORT` changes the port.
- Renamed to "Message Gateway".

### Fixed
- RabbitMQ failed to start on a fresh volume (permissions on `/data/rabbitmq`).
- `get_queue_status` returned 404 for every queue (double-encoded vhost).
- The SQLite 90-day message cleanup was never scheduled; it now runs hourly.
- Delivery workers kept writing to the old database after switching backends.

## [0.1.0] - Unreleased

Initial public release.

### Added
- Single combined Docker container: API + email/SMS/push workers + RabbitMQ,
  supervised by `supervisord`.
- Dual database backend support: local SQLite or MongoDB Atlas.
- Pluggable provider abstraction for email (Mailjet, SendGrid), SMS (Twilio,
  Infobip, local modem), and push (Pushover, ntfy).
- Dead-letter queues per channel with retry-with-backoff for transient
  delivery failures.
- MCP server exposing send, setup-wizard, and operator/debug tools.
- Scalar-powered API reference at `/scalar`.
