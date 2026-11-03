#!/bin/sh
set -eu

alembic upgrade head

# A single worker is required: the Telegram poller and the scheduler must run
# exactly once.
exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --workers 1
