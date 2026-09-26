# Relay Gateway

A self-hosted outbound messaging gateway for Email, SMS, and Push notifications —
one Docker container, one API, configured by talking to an AI agent instead of
hand-editing config files.

---

> **Private repo, not yet pushed from this machine.** The repository exists at
> `https://github.com/tadeubanzato/message-gateway` but is currently
> **private** and has not yet been pushed to from this development machine
> (network restrictions here can't reach GitHub). Anyone cloning it needs
> access granted by the repo owner, and the code needs to actually be pushed
> before the install flow below works for real. Until then, use the "run it
> locally" section further down.

## Install — the fast way (Claude Code, Codex, or any shell-capable agent)

Paste this into Claude Code, Codex CLI, or any coding agent with shell/git/docker access:

> Please install the message gateway. Instructions are at
> `https://github.com/tadeubanzato/message-gateway/blob/main/llms.txt` — fetch
> that file and follow it.

That one line stays short and stable even as install steps evolve — the real,
detailed instructions live in [`llms.txt`](llms.txt), which the agent fetches
and executes: clone the repo, build and start the container, connect to its
MCP server, and run the setup wizard, entirely conversationally.

**This only works for agents with real shell execution** (Claude Code, Codex,
and similar). It will not work in chat-only clients like Claude Desktop or
ChatGPT — those can't run `git clone` or `docker compose` themselves. If you're
using one of those, use the manual path below instead.

---

## Install — the manual way (any other client, or by hand)

```bash
git clone https://github.com/tadeubanzato/message-gateway
cd message-gateway
cp .env.example .env
docker compose up -d --build
```

## Run it locally right now (before this is pushed/published)

You already have the source on disk. From this folder:

```bash
cp .env.example .env
docker compose up -d --build
```

Wait for the container to report healthy (`docker compose ps`), then open:

```
http://localhost:8010/get-started
```

That page shows the MCP server URL and a shorter prompt:

> Connect to the MCP server at `http://localhost:8010/mcp` and run through its
> setup wizard to configure this message gateway.

Paste that into Claude Desktop, ChatGPT (once/if it supports MCP), or whatever
MCP-capable client you're using, and proceed the same way.

---

## What this is

- **One combined container**: RabbitMQ, the HTTP API, the email/SMS/push
  delivery workers, and the MCP server all run in a single Docker image,
  supervised by `supervisord`. No multi-container orchestration to reason about.
- **Two database backends, your choice**: local SQLite (default — zero external
  accounts needed, works immediately) or MongoDB Atlas (if you want managed
  backups/HA). Set `DB_BACKEND=sqlite` or `DB_BACKEND=atlas` in `.env`.
- **Pluggable providers**: swap email between Mailjet/SendGrid, SMS between
  Twilio/Infobip/your own modem, and push between Pushover/ntfy — all via env
  vars, no code changes.
- **Reliable by design**: failed deliveries retry with backoff, then land in a
  per-channel dead-letter queue instead of vanishing. Every message and delivery
  attempt is logged (auto-cleaned after 90 days) so you can see what happened.
- **MCP-native**: the same MCP server that walks you through setup also lets
  you send notifications, check queue depth, inspect recent messages and
  delivery attempts, peek at dead letters, and retry failed sends — all by
  asking your agent, instead of a web dashboard.

## Sending a message (once configured)

```bash
curl -X POST http://localhost:8010/v1/messages \
  -H "X-User-Key: <your user key>" \
  -H "X-API-Token: <your api token>" \
  -H "Content-Type: application/json" \
  -d '{"channel": "email", "to": "you@example.com", "subject": "Hi", "body": "Hello!"}'
```

Get your user key and API token by registering at `http://localhost:8010/gateway/register`.

Or, once your agent is connected via MCP, just ask it to send a notification —
it has a `send_notification` tool for exactly this.

## API reference

- Scalar (recommended, dark mode): `http://localhost:8010/scalar`
- Swagger: `http://localhost:8010/docs`

## Architecture

```
llms.txt             agent-readable install instructions (fetched by the primary install prompt)
docker-compose.yml   single service, one image
Dockerfile           RabbitMQ + Python app, supervised by supervisord
app/
  main.py            FastAPI app: HTTP API, portal, /get-started, /scalar, mounts MCP routes
  db/                 repository interface + sqlite/atlas backends
  services/
    email/            EmailProvider ABC + mailjet/sendgrid
    sms/               SmsProvider ABC + twilio/infobip/local_modem
    push/              PushProvider ABC + pushover/ntfy
  workers/            email/sms/push consumer loops (retry + DLQ)
  mcp_server/         send / setup-wizard / operator MCP tools
  routes/portal.py    login/register/account/token pages
  templates/          Jinja2 templates (portal + message templates)
```

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome. See [CHANGELOG.md](CHANGELOG.md) for release history.
This project follows [Semantic Versioning](https://semver.org/).
