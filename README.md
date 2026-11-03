# MARTY

A self-hosted personal AI assistant, controlled through a private Telegram bot.
MARTY keeps persistent memory, understands your calendar, and hosts specialised
modules: a **Personal Trainer** (training planning and tracking) and a
**C++ Professor** (study planning and accountability). It talks to you in Spanish.

## Status

**Phase 0 - architecture and project skeleton.** The backend starts, connects to
PostgreSQL, runs migrations and exposes health checks. Telegram, AI providers,
calendar and the agents come in the next phases; see the
[implementation plan](docs/implementation-plan.md).

## Quick start

Requirements: Docker with the Compose plugin.

```bash
cp .env.example .env && chmod 600 .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
docker compose up -d --build

curl -s http://127.0.0.1:8000/health/ready
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -c requirements.lock -e ".[dev]"
docker compose up -d db
.venv/bin/uvicorn --factory app.main:create_app --reload
.venv/bin/pytest
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
python-telegram-bot (long polling), APScheduler, CalDAV for iCloud Calendar,
Docker Compose. AI providers are pluggable (Gemini, Groq, Ollama).

This repository is public: never commit `.env`, backups or personal documents.
