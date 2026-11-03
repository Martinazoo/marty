# Security and Privacy

MARTY holds a personal schedule, training and health-related notes, study history,
and credentials for Telegram, iCloud and AI providers. It serves one user and must
not be reachable by anyone else.

## Secrets

- All secrets come from environment variables loaded from `.env`
  (`chmod 600`, owned by the deploying user). `.env` is git-ignored; the
  repository is **public**, so nothing secret or personal is ever committed.
- Settings hold secrets as `SecretStr`, so they do not appear in `repr()`, tracebacks
  or settings dumps.
- Secrets never enter prompts, the database, or Telegram messages. The CalDAV
  password is read from the environment by the calendar adapter only.
- Apple Calendar uses an **app-specific password** (revocable at
  appleid.apple.com), never the Apple Account password.
- The database password is generated randomly (`openssl rand`) and only used inside
  the Docker network and on `127.0.0.1`.

## Logs

- Logs are structured JSON on stdout, rotated by Docker (5 x 10 MB per container).
- `SecretRedactor` replaces every configured secret value, and anything shaped like
  a Telegram bot token, with `[REDACTED]`. It runs on the final log line, so it also
  covers exception tracebacks and third-party messages.
- `httpx`/`httpcore` are limited to `WARNING`: at `INFO` they log full request URLs,
  and Telegram URLs contain the bot token.
- ICS feed URLs can embed access tokens and are never logged.
- Logs record IDs and event types, not message contents.

## Network exposure

- No inbound ports are needed: Telegram long polling and all integrations are
  outgoing HTTPS.
- Docker publishes PostgreSQL and the HTTP API on `127.0.0.1` only.
- API docs (`/docs`, `/openapi.json`) are disabled when `APP_ENV=production`.
- The future HealthKit ingestion endpoint will require per-device tokens and will be
  exposed only through a VPN (for example Tailscale) or a TLS reverse proxy. That
  decision is made in Phase 6.

## Telegram

- Allowlist of numeric user IDs, private chats only, group joining disabled.
- Unauthorised users get a fixed reply; their messages are not processed or stored.
- Button presses are re-validated against the pending action's owner.

## LLM restrictions

- The model can only call registered tools with Pydantic-validated arguments. There
  is no SQL tool, no shell tool and no generic HTTP tool.
- Tools act only on the requesting user's records.
- Tools with the `confirm` policy (destructive changes, coach-prescribed sessions,
  bulk calendar changes) run only after the user presses a Telegram button. The
  model cannot press that button, which also limits prompt injection from imported
  documents or calendar event titles.
- Calendar writes are restricted to calendars marked `is_marty_managed`. Personal
  events are read-only.
- Tool loops are capped per message.

## Data handling

- Every state change made by MARTY is recorded in `audit_logs` with its reason.
- Soft deletion for user-facing records, so mistakes are recoverable.
- Personal calendar event descriptions are not stored.
- Data sent to cloud LLM providers is subject to their terms; see
  [ai-providers.md](ai-providers.md#what-is-sent-to-providers).

## Containers and database

- The backend image runs as an unprivileged `marty` user.
- PostgreSQL uses a dedicated database and role; it is not reachable from outside
  the host.
- Backups are written with `600` permissions into a `700` directory. Encrypt any
  copy that leaves the server (for example with `age` or `gpg`).

## Public repository checklist

Before every commit: no `.env`, no backups, no personal documents (`*.docx`,
`*.ics`), no real names, IDs or schedules in tests or fixtures. Test data must be
obviously synthetic.
