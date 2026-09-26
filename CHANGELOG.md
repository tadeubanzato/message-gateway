All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/), and this
project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

Initial public release.

### Added
- Single combined Docker container: API + email/SMS/push workers + RabbitMQ,
  supervised by `supervisord`.
- Dual database backend support: local SQLite (default, zero external
  accounts needed) or MongoDB Atlas, selected via `DB_BACKEND`.
- Pluggable provider abstraction for email (Mailjet, SendGrid), SMS (Twilio,
  Infobip, local modem), and push (Pushover, ntfy).
- Dead-letter queues per channel with retry-with-backoff for transient
  delivery failures.
- Message and delivery-attempt persistence with automatic 90-day cleanup.
- MCP server exposing send, setup-wizard, and operator/debug tools.
- `/get-started` page and Scalar-powered API reference at `/scalar`.
