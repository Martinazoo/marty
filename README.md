# MARTY

A self-hosted personal AI assistant, controlled through a private Telegram bot.
MARTY keeps persistent memory, understands your calendar, and hosts specialised
modules: a **Personal Trainer** (training planning and tracking) and a
**C++ Professor** (study planning and accountability). It talks to you in Spanish.

## Status

**Phase 2 - calendar MVP.** MARTY answers in Spanish on Telegram (text or voice
notes), remembers what you tell it across restarts (PostgreSQL), and manages your
iCloud calendars: events, weekly routines, free time, overlaps, and the calendars
themselves, plus `/today` and `/week`. It asks for confirmation through buttons
before deleting anything and falls back between Gemini models and Groq when a
provider is rate-limited. Training and study modules come next; see the
[implementation plan](docs/implementation-plan.md).

## Quick start

Requirements: Docker with the Compose plugin.

```bash
cp .env.example .env && chmod 600 .env
# Set POSTGRES_PASSWORD (openssl rand -hex 24), the Telegram token and user ID,
# and at least one AI provider key and model. Then set TELEGRAM_ENABLED=true.
docker compose up -d --build

curl -s http://127.0.0.1:8000/health/ready
```

## Development

macOS or Linux, Python 3.12 and Docker.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -c requirements.lock -e ".[dev]"
docker compose up -d db
.venv/bin/alembic upgrade head
.venv/bin/uvicorn --factory app.main:create_app --reload

.venv/bin/pytest                           # unit tests
RUN_INTEGRATION_TESTS=1 .venv/bin/pytest   # also against PostgreSQL (database marty_test)
```

## Documentation

- [Architecture](docs/architecture.md)
- [Database schema](docs/database.md)
- [Telegram integration](docs/telegram.md)
- [AI providers](docs/ai-providers.md)
- [Security and privacy](docs/security.md)
- [Deployment and operations](docs/deployment.md)
- [Implementation plan](docs/implementation-plan.md)

## Stack

Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.1 (async) + Alembic, PostgreSQL 17,
python-telegram-bot (long polling), CalDAV (httpx + icalendar) for iCloud
Calendar, Docker Compose. AI providers are pluggable (Gemini, Groq, Ollama).

This repository is public: never commit `.env`, backups or personal documents.
