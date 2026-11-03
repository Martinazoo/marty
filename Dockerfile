# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system marty \
    && useradd --system --gid marty --home-dir /app --no-create-home marty

COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY alembic.ini ./
COPY app ./app
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh

USER marty
EXPOSE 8000
ENTRYPOINT ["./scripts/entrypoint.sh"]
