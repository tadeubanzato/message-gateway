# Message Gateway

A self-hosted gateway for sending **email, SMS and push notifications** through one API.
One Docker container, one place to connect your providers, and a full log of everything
that was sent. You set it up in your browser: there is no config file to edit, and your
credentials are stored **encrypted** in the database you choose.

- **Three channels, several providers each.** Email (Mailjet, SendGrid), SMS (Twilio,
  Infobip, or your own HTTP endpoint) and push (Pushover, ntfy). Connect more than one
  per channel and pick a default, or choose per message.
- **Message log.** Every message: time, recipient, content (encrypted), status, provider,
  attempts and errors.
- **Works with AI agents.** Connect Claude Code (or any MCP client) and just say
  "send me a push notification" or "text +1 555 123 4567".
- **Reliable.** Failed deliveries retry with backoff, then land in a dead-letter queue.

---

## Contents

1. [Install](#1-install)
2. [First-run setup](#2-first-run-setup)
3. [Connect your channels](#3-connect-your-channels)
4. [Send your first message](#4-send-your-first-message)
5. [Connect an AI agent (MCP)](#5-connect-an-ai-agent-mcp)
6. [Day to day](#6-day-to-day)
7. [Security notes](#7-security-notes)
8. [Troubleshooting](#8-troubleshooting)
9. [Reference](#9-reference)

---

## 1. Install

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running) and git.

### The fast way: let an AI agent do it

Paste this into Claude Code, Codex CLI or any agent with shell access:

> Please install the message gateway. Instructions are at
> `https://github.com/tadeubanzato/message-gateway/blob/main/llms.txt`. Fetch that
> file and follow it.

The agent downloads the project, installs it, and opens the setup page in your browser.
(It needs an agent that can run shell commands; chat-only apps can't.)

### The manual way

```bash
git clone https://github.com/tadeubanzato/message-gateway ~/message-gateway
cd ~/message-gateway
./install.sh
```

`install.sh` checks Docker, builds and starts the gateway, waits until it is healthy, and
opens **http://localhost:8010** in your browser. The first build takes a few minutes.

- Another port: `MG_PORT=9000 ./install.sh`
- Don't open the browser: `MG_NO_OPEN=1 ./install.sh`
- Windows: run it from WSL or Git Bash, with Docker Desktop running.
- Update later: `git pull && ./install.sh` (your data is kept).

## 2. First-run setup

The setup page opens automatically. It has three steps:

1. **Storage.** Where the gateway keeps accounts, messages and your saved credentials.
   - **On this computer** (default): local SQLite. Nothing else needed.
   - **MongoDB Atlas**: paste your connection string and a database name. In Atlas, allow
     your IP under *Network Access* and use a user with read/write access. If the database
     already holds a gateway's data, you simply reconnect to it.
2. **Create the administrator account.** Enter a name, email and a strong password. The
   administrator can change all settings, connect and add email/SMS/push providers, manage
   API keys and decide whether anyone else may sign up. Passwords can't be reset by email,
   so keep it safe. You can also choose whether to keep a full message log (on by default).
3. **Save your API key.** Your user key and API token are shown **once**. Store them in a
   password manager. This screen also shows a ready-made command to connect an AI agent
   (see [section 5](#5-connect-an-ai-agent-mcp)).

You land on the **Home** page, which shows each channel's status. Next, connect a channel.

> Coming from an older install? **Settings → Import from an existing .env file** reads your
> old `.env`, saves the provider settings it finds (encrypted), and turns old SMS modem
> settings into a custom SMS endpoint.

## 3. Connect your channels

Open **Channels** in the top menu (administrator only) and choose Email, SMS or Push. Pick your
provider (for example Pushover), and you see only that provider's form, with links (opening in a
new tab) to sign in and find your keys. Fill in the fields and click **Connect**. The gateway checks the
credentials, then **Send test** lets you confirm a real message arrives.

You can connect several providers on one channel: use **+ Add another provider**. One is the
**default**; click **Make default** to change it. Saved secrets are never shown again, but the last 4 characters appear beside each field
(`••••••••1a2b`) so you can compare with the key in your provider's dashboard. Type a new
value to replace one, or leave the field blank to keep it.

### Email

| Provider | What you need |
|---|---|
| **Mailjet** | API key and secret key (Account → REST API → API Key Management), and a **verified** sender address |
| **SendGrid** | An API key (Settings → API Keys) and a **verified** sender address |

### SMS

| Provider | What you need |
|---|---|
| **Twilio** | Account SID and auth token (Twilio console), and a Twilio phone number to send from |
| **Infobip** | API key, your base URL and a sender ID (Infobip portal) |
| **Custom HTTP endpoint** | Your own SMS service. See below |

**Your own SMS endpoint.** Choose *Custom HTTP endpoint*, then enter:

- **Endpoint URL** and **HTTP method** (POST, PUT or PATCH)
- **Headers**, one per line, for example `Authorization: Bearer <token>` (stored encrypted)
- **Request body**, a sample JSON containing placeholders. The gateway sends every message
  in exactly this pattern:

  | Placeholder | Replaced with |
  |---|---|
  | `{{to}}` | the phone number, e.g. `+15551234567` |
  | `{{to_digits}}` | digits only, e.g. `15551234567` |
  | `{{message}}` | the message text |
  | `{{message_id}}` | the gateway's id for the message |

  ```json
  {"number": "{{to}}", "message": "{{message}}"}
  ```

- Optionally, text the reply **must contain** to count as delivered (for example
  `"status": "ok"`). Without it, any 2xx reply counts.

A live preview shows the request with a sample number and message, and **Fill in an example**
loads a starting point. Placeholders are filled in *inside* the JSON, so quotes or newlines in
a message can't break the request. The gateway calls your URL from this machine, so it must be
reachable from where Docker runs.

**Default phone number.** On the SMS page, *Channel defaults* lets you set a number (any format,
country code required). It is used whenever a message doesn't say who to text.

### Push

| Provider | What you need |
|---|---|
| **Pushover** | Your user key, plus one or more **applications** (below) |
| **ntfy** | A topic name (pick a long, hard-to-guess one) and optionally a server URL. Free, no account. Subscribe to the topic in the ntfy app |

**Pushover.** The form is short: your **user key**, your **default app** (a name and its API token),
and a **+ Add another app** button for more. Create each app at pushover.net/apps/build. Give an
app any name you like and use it as `"app"` when sending; messages that name no app use the
default app. On an extra app, **Make default** swaps it into the default slot. Names may contain
letters, numbers, dots, dashes and underscores. Tokens are stored encrypted, in this form:

```
PUSHOVER_APPS=alerts:PUSHOVER_APPTOKEN_ALERTS,backups:PUSHOVER_APPTOKEN_BACKUPS
```

## 4. Send your first message

Every request needs two headers: `X-User-Key` (your account, `gw_user_...`, never changes) and
`X-API-Token` (the secret for one key, `gw_tok_...`). Get both from the web app: sign in, open
**API keys**, copy your user key, create a key (one per program that sends) and copy its token
straight away, because **it is shown only once** and only a hash is stored. Lost it? Click
**Replace token**; the old one stops working immediately. There is no login call: the headers go on
every request, and a wrong or revoked pair gets a `401`. Then:

```bash
curl -X POST http://localhost:8010/v1/messages/push \
  -H "X-User-Key: <your user key>" \
  -H "X-API-Token: <your api token>" \
  -H "Content-Type: application/json" \
  -d '{"subject": "Hello", "body": "It works!"}'
```

Each channel has its own endpoint: `POST /v1/messages/push`, `/v1/messages/email`,
`/v1/messages/sms`, and `/v1/messages/email/template` and `/v1/messages/sms/template` for saved templates.
The API reference at `/scalar` documents each one.

| Channel | Fields |
|---|---|
| `push` | `body`; optional `subject` (title), `app` (Pushover app **name**), `device`, `url`, `url_title`, `to` |
| `email` | `to`, `subject`, `body`; optional `emailType` (`txt` or `html`) |
| `sms` | `body`; `to`, or omit it to use the default phone number |

Every channel also accepts:

- **`provider`**: pick a connected provider for this message, e.g. `"provider": "sendgrid"`.
  Without it the channel's default is used.
- **`template` + `context`** (email and SMS): use a saved template instead of `body`. See below.

### Templates (email and SMS)

Templates are plain files you keep in `app/templates/email/` (`<name>.txt` and/or `<name>.html`) and
`app/templates/sms/` (`<name>.txt`). A template uses `{{ context.key }}` placeholders:

```
Hi {{ context.name }}, welcome! Your account is now active.
```

Send it by name, with the values in `context`:

```json
{ "to": "+15551234567", "template": "welcome", "context": { "name": "Ana" } }
```

Ana receives: `Hi Ana, welcome! Your account is now active.`

For email, the same idea with a subject (placeholders work there too):

```json
{ "to": "ana@example.com", "subject": "Welcome, {{ context.name }}", "template": "welcome", "context": { "name": "Ana" } }
```

Ana receives the subject `Welcome, Ana` and the `welcome.txt` body with her name filled in. Add
`"emailType": "html"` to use `welcome.html` instead.

- **Email**: `emailType` picks the file (`html` uses `<name>.html`, otherwise `<name>.txt`), so provide
  both files if you send both. Placeholders also work in `subject`.
- **Strict by default**: a placeholder with no value in `context` returns a 400. Set
  `TEMPLATE_STRICT=false` to fill it with an empty string instead. An unknown template name is a 400.
- The folders are mounted into the container, so new templates are picked up without a rebuild.
- Push notifications don't use templates; send `body` directly.

`to` may be a list. If nothing is connected for the channel, the API answers immediately with
a clear error instead of queueing a message that can't be delivered.

## 5. Connect an AI agent (MCP)

The MCP server lets an AI agent send messages and check delivery. It uses the **same key and
token** as the API, so connecting is one command. The last setup screen (and every new key)
shows it ready to copy. Run it **in your own terminal** (it contains your token), then restart
Claude Code or run `/mcp`:

```bash
claude mcp add --transport http message-gateway http://localhost:8010/mcp \
  --header "X-User-Key: <your user key>" --header "X-API-Token: <your api token>"
```

Then just ask, for example:

- "Send me a push notification saying the deploy finished."
- "Send a test email."
- "Send an SMS to +1 555 123 4567 saying I'm running late."
- "Text me that dinner is ready." (uses the default phone number)
- "Which providers are connected? Did my last message get delivered?"

Each send waits a few seconds and reports **delivered** or **failed** with the reason. Phone
numbers can be typed any way (`+1 (555) 123-4567`); a number without a country code is never
guessed, the agent is told to ask for it.

| Tool | Use |
|---|---|
| `send_push`, `send_email`, `send_sms`, `send_test` | The common sends. `send_test` with no address emails **you** |
| `send_notification` | Any channel, templates, full control |
| `list_providers`, `get_setup_status`, `get_setup_instructions`, `get_health` | See what's connected and where to set up the rest |
| `list_recent_messages`, `get_message`, `list_delivery_attempts` | Your own messages and their delivery |
| `check_provider_config`, `get_queue_status`, `list_dead_letters`, `retry_dead_letter` | **Administrator only** |

Provider credentials are never handled through MCP: they are entered in the web app.

## 6. Day to day

- **Home**: channel status, recent messages, a copyable send example.
- **Message log**: search and filter everything sent; click a message for its delivery attempts.
  Messages sent with **Send test** are logged too, with a small **Test** label, and a
  **Hide tests** filter.
- **API keys**: create, replace or delete keys. A replaced token stops working immediately.
- **Settings** (user menu, administrator): keep or drop message content, allow sign-ups, import an old `.env`.
- **Account** (user menu): your profile and password.

Manage the container from the project folder:

```bash
docker compose stop            # stop
docker compose start           # start again
docker compose logs gateway    # see what's happening
./install.sh                   # rebuild after updating (data is kept)
docker compose down -v         # UNINSTALL: deletes local data and the encryption key
```

## 7. Security notes

- **Localhost only.** The gateway listens on `127.0.0.1` because the first-run setup page has
  no login. To reach it from other machines, change the port mapping in `docker-compose.yml`
  *after* setup, and put it behind HTTPS.
- **Administrator vs members.** The first account is the administrator. Sign-ups are off by
  default. Members can send messages but can't change settings. Everything under Channels and
  Settings requires the administrator's login session.
- **Back up your data.** With local storage, your database *and the key that decrypts your
  saved credentials* live in the `gateway_data` Docker volume. Back it up with
  `docker compose cp gateway:/app/data ./gateway-backup` and keep that copy private. If the
  key is lost while a remote database is kept, the app tells you which credentials can't be
  read so you can enter them again.
- **MCP uses your API key.** Tools act only on your own messages; queue, dead-letter and
  provider-check tools are administrator-only.
- Recipients (emails, phone numbers) are stored in plain text so they can be searched.
  Subjects, bodies and credentials are encrypted.
- The gateway calls provider APIs, and your custom SMS URL, from your machine.

## 8. Troubleshooting

| Symptom | What to do |
|---|---|
| `install.sh`: "Docker is not running" | Start Docker Desktop, wait until it says it's running, run it again |
| "Port 8010 is already in use" | `MG_PORT=9000 ./install.sh` |
| Setup page doesn't open | Open `http://localhost:8010` yourself |
| Red banner: "not on a persistent volume" | You started it without `install.sh`; use `./install.sh` so data survives |
| "N saved credentials can't be read" | The encryption key changed (volume replaced); enter them again on the Channels pages. Old API keys may need re-creating |
| A send returns "No … provider is connected" | The administrator connects one under Channels |
| Mail is rejected | The sender address must be verified with Mailjet/SendGrid |
| SMS says "failed" with your endpoint | Check the URL, headers and body preview; add or fix the "response must contain" text |
| Forgot the administrator password | Passwords can't be reset by email; restore from a backup, or reinstall with `docker compose down -v` |
| Anything else | `docker compose logs gateway` |

## 9. Reference

### Advanced: `.env` overrides

You don't need a `.env` file. To pin a setting from the environment, copy `.env.example` to
`.env`, uncomment only what you need, and run `docker compose up -d --force-recreate`
(`restart` doesn't reload it). Anything set there **overrides** the browser settings, which
then can't change it (the Channels page tells you when that happens).

### API documentation

The API reference is at **`http://localhost:8010/scalar`** (also linked from the **About** page). Click
**Authenticate** to enter your user key and API token, then use **Test Request** on the send endpoint.

### Architecture

```
install.sh           builds, starts and opens the setup page
llms.txt             instructions an AI agent follows to install this
docker-compose.yml   one service, two volumes, localhost-only port
Dockerfile           RabbitMQ + Python app, supervised by supervisord
app/
  main.py            FastAPI app: messages API, mounts the MCP server
  bootstrap.py       auto-generated keys and the DB choice (in the data volume)
  db/                repository interface + sqlite / atlas backends
  routes/            first-run setup, login and keys, portal pages
  services/          channels, encrypted settings, message log, providers
    sms/custom_http  your own SMS endpoint (URL, headers, JSON pattern)
  workers/           email / sms / push consumers (retry + dead letters)
  mcp_server/        MCP tools and their key-based login
  templates/, static/  pages, shared stylesheet and script
```

## License

MIT, see [LICENSE](LICENSE). Release history is in [CHANGELOG.md](CHANGELOG.md).
