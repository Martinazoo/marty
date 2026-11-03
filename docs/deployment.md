# Deployment and Operations

MARTY runs on an Ubuntu server with Docker Compose: one backend container and one
PostgreSQL container. No public domain or open inbound port is required.

## Requirements

- Ubuntu Server (tested on 24.04).
- Docker Engine with the Compose plugin (`docker compose version`).
- Git.
- About 1 GB of free disk space for images, plus database and backups.

## Installation

```bash
git clone git@github.com:Martinazoo/marty.git
cd marty

cp .env.example .env
chmod 600 .env
# Set a strong database password (on macOS use: sed -i '' ...):
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
# For the server, also set:
#   APP_ENV=production
#   LOG_FORMAT=json
#   TELEGRAM_ENABLED=true, TELEGRAM_BOT_TOKEN (the production bot),
#   TELEGRAM_ALLOWED_USER_IDS
#   GEMINI_API_KEY / GEMINI_MODEL, GROQ_API_KEY / GROQ_MODEL

docker compose up -d --build
```

Verify:

```bash
docker compose ps                          # both services "healthy"
curl -s http://127.0.0.1:8000/health       # {"status":"ok",...}
curl -s http://127.0.0.1:8000/health/ready # {"status":"ok","checks":{"database":"ok"}}
```

Database migrations run automatically every time the backend starts
(`alembic upgrade head` in `scripts/entrypoint.sh`).

## Start on boot

Both services use `restart: unless-stopped`. Make sure Docker itself starts on
boot:

```bash
sudo systemctl enable docker containerd
```

After a reboot, Docker restarts the containers automatically, unless you stopped
them yourself with `docker compose stop` before the reboot.

## Day-to-day operations

```bash
docker compose logs -f backend        # follow logs (JSON lines)
docker compose restart backend        # restart the application
docker compose down                   # stop everything (data is kept in the volume)
docker compose exec db psql -U marty marty   # database shell
```

Never run `docker compose down -v` on the server: `-v` deletes the database volume.

## Updating

```bash
git pull
docker compose up -d --build
```

## Backups

`scripts/backup_db.sh` writes a compressed `pg_dump` (custom format) to `./backups`
and deletes dumps older than 14 days (`BACKUP_RETENTION_DAYS`).

```bash
scripts/backup_db.sh                  # or: scripts/backup_db.sh /path/to/backups
```

Nightly at 03:30, with `crontab -e`:

```cron
30 3 * * * cd /path/to/marty && scripts/backup_db.sh >> backups/backup.log 2>&1
```

Copy backups off the server regularly, encrypted. A backup that only lives on the
same disk does not protect against disk failure.

### Restore

```bash
docker compose stop backend
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
    < backups/marty_YYYYMMDDTHHMMSSZ.dump
docker compose start backend
```

Test a restore occasionally, for example into a temporary container.

## Local development

On macOS: `brew install python@3.12` and a Docker runtime (OrbStack is used for
this project; Docker Desktop or Colima also work).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -c requirements.lock -e ".[dev]"

docker compose up -d db                # database only, on 127.0.0.1:5432
.venv/bin/alembic upgrade head
.venv/bin/uvicorn --factory app.main:create_app --reload

.venv/bin/pytest                       # unit tests
RUN_INTEGRATION_TESTS=1 .venv/bin/pytest   # also against PostgreSQL (marty_test database)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Or run the whole stack exactly as on the server: `docker compose up -d --build`.

Notes:

- When running outside Docker, keep `POSTGRES_HOST=localhost` in `.env`. Compose
  overrides it to `db` for the backend container.
- Run only one backend per bot token: Telegram accepts a single long-polling
  connection per token (a second one gets *409 Conflict*). Use a separate
  development bot on the development machine, and do not run the local and the
  Docker backend at the same time.
- Integration tests drop and recreate the `<POSTGRES_DB>_test` database; they never
  touch the development data.

### Database migrations

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"
# review the generated file in app/database/migrations/versions/
.venv/bin/alembic upgrade head
```

### Dependencies

Ranges live in `pyproject.toml`; exact runtime pins used by the Docker image live
in `requirements.lock`. After changing dependencies:

```bash
scripts/lock_requirements.sh   # resolves inside the Dockerfile's base image
```
