# Message Gateway

A self-hosted gateway for sending **email, SMS, push, Telegram and WhatsApp messages** through
one API. One Docker container, one place to connect your providers, and a full log of everything
that was sent. You set it up in your browser: there is no config file to edit, and your
credentials are stored **encrypted** in the database you choose.

- **Five channels, several providers each.** Email (Mailjet, SendGrid), SMS (your own HTTP
  endpoint, Twilio, Infobip or Sinch), push (Pushover, ntfy), Telegram (a bot you create) and
  WhatsApp (the official WhatsApp Business Platform). Connect more than one per channel, pick a
  default, or choose per message - and turn any connected provider on or off without disconnecting it.
- **Dashboard.** A per-channel message trend chart on Home, and a full message log: time,
  recipient, content (encrypted), status, provider, attempts and errors.
- **Works with AI agents.** Connect Claude Code (or any MCP client) and just say
  "send me a push notification" or "text +1 555 123 4567". It can also check delivery status,
  read recent messages and help troubleshoot a failed send.
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
- Who can reach it: on a headless Linux server (or over SSH) the installer makes it reachable from
  your network automatically and prints the address to open. On a desktop it is this machine only.
  Force either way with `MG_BIND=0.0.0.0 ./install.sh` or `MG_BIND=127.0.0.1 ./install.sh`
  (see the security note below)
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
   administrator can change all settings, connect and add email/SMS/push/Telegram/WhatsApp providers,
   manage API keys and decide whether anyone else may sign up. Passwords can't be reset by
   email, so keep it safe. You can also choose whether to keep a full message log (on by default).
3. **Save your API key.** Your user key and API token are shown **once**. Store them in a
   password manager. This screen also shows a ready-made command to connect an AI agent
   (see [section 5](#5-connect-an-ai-agent-mcp)).

You land on the **Home** page, which shows each channel's status. Next, connect a channel.

> Coming from an older install? **Settings → Import from an existing .env file** reads your
> old `.env`, saves the provider settings it finds (encrypted), and turns old SMS modem
> settings into a custom SMS endpoint.

## 3. Connect your channels

Open **Channels** in the top menu (administrator only) and choose Email, SMS, Push, Telegram or WhatsApp. Pick your
provider (for example Pushover), and you see only that provider's form, with links (opening in a
new tab) to sign in and find your keys. Fill in the fields and click **Connect**. The gateway checks the
credentials, then **Send test** lets you confirm a real message arrives.

You can connect several providers on one channel: use **+ Add another provider**. One is the
**default**; click **Make default** to change it. Saved secrets are never shown again, but the last 4 characters appear beside each field
(`••••••••1a2b`) so you can compare with the key in your provider's dashboard. Type a new
value to replace one, or leave the field blank to keep it.

**Turn a provider on or off.** Every connected provider's card has a colored **Enabled**/**Disabled**
pill - click it to flip it. A disabled provider stays connected and configured, it's just refused
for sending: a message that would use it (named explicitly, or picked as the channel's default)
gets a `403` with a clear reason, everywhere (the API, MCP, and **Send test**). The Home page and
the channel page show green **Ready** when every connected provider is on, amber **Ready** when
only some are, and red **Disabled** when all of them are off.

### Email

| Provider | What you need |
|---|---|
| **Mailjet** | API key and secret key (Account → REST API → API Key Management), and a **verified** sender address |
| **SendGrid** | An API key (Settings → API Keys) and a **verified** sender address |

### SMS

**Custom API endpoint** is the default - most people either have their own SMS service already or
would rather not create a Twilio/Infobip/Sinch account just to try the gateway.

| Provider | What you need |
|---|---|
| **Custom API endpoint** | Your own SMS service. See below |
| **Twilio** | Account SID and auth token (Twilio console), and a Twilio phone number to send from |
| **Infobip** | API key, your account's base URL and a sender ID (Infobip portal) |
| **Sinch** | Service Plan ID, an API token, the region your plan was created in (US/EU/AU/BR/CA), and a from number or sender ID (Sinch dashboard) |

**Your own SMS endpoint.** Choose *Custom API endpoint*, then either paste a working `curl`
command for your service and click **Fill in from curl** - it fills in the URL, method and
headers, and swaps recognizable body fields (`number`/`to`/`phone`, `message`/`text`/`body`,
`message_id`/`id`/`ref`...) for the gateway's placeholders - or fill the fields in yourself:

- **Endpoint URL** and **HTTP method** (POST, PUT or PATCH)
- **Headers**, one per line, for example `Authorization: Bearer <token>` (stored encrypted)
- **Request body**, a sample JSON containing placeholders. The gateway sends every message
  in exactly this pattern:

  | Placeholder | Replaced with |
  |---|---|
  | `{{to}}` | the phone number, e.g. `+15551234567` |
  | `{{to_digits}}` | digits only, e.g. `15551234567` |
  | `{{message}}` | the message text |
  | `{{message_id}}` | the gateway's id for the message (the same one in the message log and on the RabbitMQ payload, for troubleshooting) |

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

### Telegram

| Provider | What you need |
|---|---|
| **Telegram Bot** | A bot token from @BotFather (the only way to send Telegram messages) |

In Telegram, open **@BotFather** and send `/newbot`. Follow the prompts to name your bot;
BotFather replies with a **bot token** that looks like `123456789:AAH-...` - paste it in the web
app. That's the whole setup on Telegram's side.

**Finding a chat ID.** Telegram bots can't message someone first: whoever should receive
messages has to open your bot and send it anything once. After that, save your bot token and
click **Find chat IDs** on the Telegram page - it looks up everyone who's recently messaged your
bot and lets you click one to fill in a chat ID, no copying numbers out of raw JSON.

**Default chat ID.** Like SMS's default phone number, *Channel defaults* on the Telegram page
lets you set a chat ID used whenever a message doesn't say who to notify.

### WhatsApp

| Provider | What you need |
|---|---|
| **WhatsApp Business Platform** | A Phone Number ID and access token from a Meta developer app (the official Cloud API - not a third-party wrapper) |

1. Create an app at [developers.facebook.com](https://developers.facebook.com/) and add the
   **WhatsApp** product.
2. Its API Setup page shows a **Phone Number ID** and a temporary access token - the Phone
   Number ID is permanent, paste it in the web app.
3. That temporary token expires in 24 hours. For one that keeps working, go to **Business
   Settings → System users**, create a system user, assign it the app and the WhatsApp Business
   Account with full control, and **Generate token** there instead. Paste that token in the web app.

**The 24-hour window.** This is WhatsApp's own rule, not something the gateway adds: a free-form
message only delivers if the recipient has messaged your WhatsApp number in the last 24 hours.
Anyone else needs a pre-approved message *template* (an HSM), which this gateway doesn't send -
sending to someone outside the window fails with a clear error explaining why, not a silent drop.

**Default recipient.** *Channel defaults* on the WhatsApp page lets you set a phone number used
whenever a message doesn't say who to notify - same 24-hour rule applies to it.

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
`/v1/messages/sms`, `/v1/messages/telegram`, `/v1/messages/whatsapp`. Email and SMS also have a
`/template` variant (e.g. `/v1/messages/sms/template`) for saved templates - push, Telegram and
WhatsApp don't use templates, just send `body` directly. The API reference at `/scalar` documents
each one.

| Channel | Fields |
|---|---|
| `push` | `body`; optional `subject` (title), `app` (Pushover app **name**), `device`, `url`, `url_title`, `to` |
| `email` | `to`, `subject`, `body`; optional `emailType` (`txt` or `html`) |
| `sms` | `body`; `to`, or omit it to use the default phone number |
| `telegram` | `body`; `to` (a chat ID), or omit it to use the default chat ID |
| `whatsapp` | `body`; `to`, or omit it to use the default recipient - only delivers within the 24-hour window |

Every channel also accepts:

- **`provider`**: pick a connected provider for this message, e.g. `"provider": "sendgrid"`.
  Without it the channel's default is used.
- **`template` + `context`** (email and SMS only): use a saved template instead of `body`. See below.

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
- Push, Telegram and WhatsApp don't use templates; send `body` directly.

`to` may be a list. If nothing is connected for the channel, the API answers immediately with
a clear error instead of queueing a message that can't be delivered.

## 5. Connect an AI agent (MCP)

The MCP server lets an AI agent send messages, check delivery, and help troubleshoot. It uses
the **same key and token** as the API, so connecting is one command. The last setup screen (and
every new key) shows it ready to copy. Run it **in your own terminal** (it contains your token),
then restart Claude Code or run `/mcp`:

```bash
claude mcp add --transport http message-gateway http://localhost:8010/mcp \
  --header "X-User-Key: <your user key>" --header "X-API-Token: <your api token>"
```

(Use your gateway's real address here - a server IP, a domain, a tunnel - not `localhost`, unless
that's genuinely where you're running it. The **About** page in the web app always shows yours
correctly, plus a short prompt you can paste into any MCP-capable agent instead of the command
above.)

Then just ask, for example:

- "Send me a push notification saying the deploy finished."
- "Send a test email."
- "Send an SMS to +1 555 123 4567 saying I'm running late."
- "Text me that dinner is ready." (uses the default phone number)
- "Send that to me on Telegram instead." (uses the default chat ID)
- "WhatsApp me the tracking number." (only works if you've messaged the bot's WhatsApp number in the last 24 hours)
- "Which providers are connected? Did my last message get delivered?"

Each send waits a few seconds and reports **delivered** or **failed** with the reason. Phone
numbers can be typed any way (`+1 (555) 123-4567`); a number without a country code is never
guessed, the agent is told to ask for it. If the provider that would send it (named, or the
channel's default) is turned off, the agent gets a clear "X is turned off on the gateway" reason
instead - the same `403` the HTTP API returns.

| Tool | Use |
|---|---|
| `send_push`, `send_email`, `send_sms`, `send_telegram`, `send_whatsapp`, `send_test` | The common sends. `send_test` with no address emails **you** |
| `send_notification` | Any channel, templates, full control |
| `list_providers`, `get_setup_status`, `get_setup_instructions`, `get_health` | See what's connected and where to set up the rest |
| `list_recent_messages`, `get_message`, `list_delivery_attempts` | Your own messages and their delivery |
| `check_provider_config`, `get_queue_status`, `list_dead_letters`, `retry_dead_letter` | **Administrator only** |

Provider credentials are never handled through MCP: they are entered in the web app.

## 6. Day to day

- **Home**: channel status (including a red/amber/green read on whether any connected provider
  is turned off), a messages-by-channel trend chart, a copyable send example.
- **Message log**: search and filter everything sent; click a message for its delivery attempts.
  Messages sent with **Send test** are logged too, with a small **Test** label, and a
  **Hide tests** filter.
- **API keys** (user menu): click an app's name to rename it in place - it saves itself, no
  Save button. Create, replace or delete keys; a replaced token stops working immediately.
- **Settings** (user menu, administrator): keep or drop message content, allow sign-ups, set a
  public address override for the About page and MCP URL (see [Reference](#9-reference)),
  import an old `.env`.
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

- **Localhost on a desktop, network-visible on a headless server.** The first-run setup page has
  no login. On a desktop the gateway therefore listens on `127.0.0.1` only. On a headless server
  `./install.sh` opens it to your network (your browser is on another computer) and protects the
  setup page with a one-time token: the installer prints a link ending in `#token=...`, and only
  someone with that link can create the administrator account. Put it behind HTTPS if it faces
  anything beyond your own network. Override with `MG_BIND=127.0.0.1` or `MG_BIND=0.0.0.0`.
- **Administrator vs members.** The first account is the administrator. Sign-ups are off by
  default. Members can send messages but can't change settings. Everything under Channels and
  Settings requires the administrator's login session.
- **Back up your data.** With local storage, your database *and the key that decrypts your
  saved credentials* live in the `gateway_data` Docker volume. Back it up with
  `docker compose cp gateway:/app/data ./gateway-backup` and keep that copy private. If the
  key is lost while a remote database is kept, the app tells you which credentials can't be
  read so you can enter them again.
- **MCP uses your API key.** Tools that read messages (`list_recent_messages`, `get_message`,
  `list_delivery_attempts`) only see messages sent under the connecting account's own keys, even
  for the administrator; queue, dead-letter and provider-check tools are administrator-only.
- **Turning a provider off doesn't remove it.** Its credentials stay saved and encrypted; a
  disabled provider just refuses to send until you turn it back on.
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
| A send returns "… is turned off on the gateway" | That provider is connected but switched off - click its Enabled/Disabled pill on the Channels page to turn it back on, or pick a different connected provider with `"provider"` |
| Mail is rejected | The sender address must be verified with Mailjet/SendGrid |
| SMS says "failed" with your endpoint | Check the URL, headers and body preview; add or fix the "response must contain" text |
| Telegram "Find chat IDs" shows nothing | Open your bot in Telegram and send it any message first - bots can't message someone who hasn't messaged them |
| Telegram send fails ("chat not found" / "bot was blocked") | The chat ID is wrong, or that user blocked/never started the bot - use **Find chat IDs** to get a fresh one |
| WhatsApp send fails ("more than 24 hours have passed") | The recipient hasn't messaged your WhatsApp number recently enough - free-form messages need a reply within the last 24 hours; a message template would be required otherwise, which isn't supported here |
| WhatsApp send fails with an OAuth/token error | The access token expired (the 24-hour one from the API Setup page) or is wrong - generate a permanent one via a System User instead (see the WhatsApp section above) |
| Forgot the administrator password | Passwords can't be reset by email; restore from a backup, or reinstall with `docker compose down -v` |
| Anything else | `docker compose logs gateway` |

## 9. Reference

### Advanced: `.env` overrides

You don't need a `.env` file. To pin a setting from the environment, copy `.env.example` to
`.env`, uncomment only what you need, and run `docker compose up -d --force-recreate`
(`restart` doesn't reload it). Anything set there **overrides** the browser settings, which
then can't change it (the Channels page tells you when that happens).

**Public address.** The About page and the MCP URL default to whatever address you're browsing
from - a server IP, a domain, a tunnel address all just work automatically. If you're behind a
reverse proxy or tunnel that doesn't forward the original address, so the gateway only ever sees
its own internal one, set it once in **Settings → Public address** (or `PUBLIC_BASE_URL` in
`.env` for a locked-down install).

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
    sms/sinch, twilio, infobip  the other SMS providers
    telegram/bot_api  Telegram Bot API (send, check token, find chat ids)
    whatsapp/cloud_api  WhatsApp Business Platform / Meta Cloud API
  workers/           email / sms / push / telegram / whatsapp consumers (retry + dead letters)
  mcp_server/        MCP tools and their key-based login
  templates/         pages (Jinja)
  static/            shared stylesheet and script; static/vendor/ has Chart.js,
                     vendored so the gateway has no runtime CDN dependency
```

## License

MIT, see [LICENSE](LICENSE). Release history is in [CHANGELOG.md](CHANGELOG.md).
