# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/), and this
project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - Unreleased

### Added
- **Email template builder** (Templates in the top menu, administrators, once Email is set up): paste
  or upload HTML (or text), edit it right in the preview, select any text to turn it into a
  `{{ context.name }}` placeholder, and see the request payload your app should send. Each saved template gets an ID (`tpl_...`); the
  sending app passes it as `template` with `context`. `template` also still accepts a name.
  `GET /v1/templates/email` and `GET /v1/templates/email/{template}` (now in the API reference under
  **Templates**, with the other lookup) list the IDs and the context keys each needs, with an example request. Templates are saved in the database (SQLite or MongoDB),
  so they move with the rest of your data.
- **Per-channel send endpoints** in the API reference, grouped Email / SMS / Push: plain and template
  variants for email (`/v1/messages/email`, `/email/template`) and SMS (`/sms`, `/sms/template`), and
  `/push`. Each has only its own fields and examples; the template endpoints document the
  `{{ context.name }}` placeholders, `.txt`/`.html` files and strict missing-key errors.
  `POST /v1/messages` still works but is no longer listed.
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
  are closed by default. Everything under Channels and Settings is administrator-only
  (the logged-in session is enough; no password prompts).

- **Pushover applications**: add any number of named apps, each with its own token,
  in the web app, plus a default app. Sends and MCP calls can name an `app`.
- **Custom SMS endpoint** (replaces the fixed "local modem" provider): set your own URL,
  headers and JSON body pattern with `{{to}}`, `{{message}}` placeholders and a live preview.
- **Default SMS phone number**, used when a message names no recipient.
- **Import from `.env`** in Settings for migrating older installs.
- Sign-in and API-key links (open in a new tab) on every provider card.
- MCP: `send_push`, `send_email`, `send_sms` and `send_test` tools that wait for and report the
  delivery result; free-form phone numbers are normalized (country code required).

- **Guided channel pages**: choose a provider, then see only that provider's form; connected
  providers show as compact cards with Send test, Make default, Edit and Remove.
- One API reference (Scalar at `/scalar`), documenting only the send-message endpoint
  (`POST /v1/messages`) with a login box for your key and ready-to-run examples. Swagger and ReDoc are gone; the portal's own
  pages no longer clutter it.
- **Change database** in Settings: move between SQLite and Atlas (or another Atlas database) with your
  accounts, credentials and message history copied over, verified, and you stay signed in.
- Every account carries a visible `role` (`admin` or `member`) in the database.
- Saved credentials show their **last 4 characters** (`••••••••1a2b`) beside each field.
- **Send test** goes through the real pipeline, so tests appear in the message log and
  Home, labelled **Test** (with a Hide tests filter).
- Messages sent through MCP are tagged `source: mcp` and labelled **MCP** in the message log,
  next to the existing **Test** label (an MCP-sent test carries both).
- MCP: `get_setup_status` now reports the database too (backend, and its MongoDB host/database
  or SQLite path/size - no credentials), always computed fresh. `list_providers` reports each
  channel's state and, when it isn't connected, a `setup_page` link; the agent is told to offer
  opening it in the user's browser instead of just describing where to go, and the same happens
  when a send fails for a not-connected channel.
- MCP, administrator-only: `get_broker_log` tails RabbitMQ's own log file for broker-level
  troubleshooting (RabbitMQ itself keeps no history of consumed messages - that's the message
  log's job). `query_database` runs a raw read-only Mongo query against the `messages`/`attempts`
  collections on the Atlas backend, for filters the other tools don't cover; on SQLite it points
  back to `list_recent_messages`/`get_message`.
- `GET /setup/mcp-command`: a one-time, in-memory-only handoff of the MCP connection info for the
  account `/setup/admin` just created, so `llms.txt` installs (Claude Code, e.g.) can run
  `claude mcp add` automatically right after the browser step, with no copy-paste and the key
  never touching a file or the chat. Works once, expires after 10 minutes unclaimed; the manual
  copy-from-the-Keys-page flow still works exactly as before as a fallback.

### Changed
- `.env` is now optional and only for advanced overrides; every variable in
  `.env.example` is commented out. Variables set in `.env` override the browser
  settings.
- The gateway port is bound to `127.0.0.1` by default. `MG_PORT` changes the port.
- Renamed to "Message Gateway".

### Fixed
- MCP requests to any hostname other than `localhost`/`127.0.0.1` (e.g. `okame.local`, a LAN
  IP, a tunnel) got `421 Invalid Host header` and could never connect - FastMCP's DNS-rebinding
  check was on by default. Off by default now (`/mcp` already requires the API key and token);
  set `MCP_ALLOWED_HOSTS` to turn it back on for specific hosts.
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
