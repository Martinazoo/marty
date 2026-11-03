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
# Set a strong database password:
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
# For the server, also set:
#   APP_ENV=production
#   LOG_FORMAT=json
# Then fill in the Telegram and AI values when Phase 1 is deployed.

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

```bash
python3 -m venv .venv
.venv/bin/pip install -c requirements.lock -e ".[dev]"

docker compose up -d db                # database only, on 127.0.0.1:5432
.venv/bin/uvicorn --factory app.main:create_app --reload

.venv/bin/pytest                       # unit tests
RUN_INTEGRATION_TESTS=1 .venv/bin/pytest   # also tests against the running database
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Notes:

- When running outside Docker, keep `POSTGRES_HOST=localhost` in `.env`. Compose
  overrides it to `db` for the backend container.
- Do not run the local backend and the Docker backend at the same time: they would
  compete for port 8000 and, from Phase 1, for the same Telegram bot token. Use a
  separate development bot token locally.

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
scripts/lock_requirements.sh
```
