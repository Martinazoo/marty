import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.database.base import Base

ROOT = Path(__file__).resolve().parent.parent


def make_settings(**overrides: object) -> Settings:
    """Settings isolated from the developer's `.env` file."""
    values: dict[str, object] = {"app_env": "test", "log_format": "json"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION_TESTS=1 to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# --- Integration fixtures: a dedicated `<POSTGRES_DB>_test` database ----------


async def _recreate_database(settings: Settings) -> None:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value() or None,
        database="postgres",
    )
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{settings.postgres_db}" WITH (FORCE)')
        await connection.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def database_settings() -> Settings:
    """Credentials from `.env`, pointed at a throwaway test database."""
    settings = Settings(app_env="test")
    return settings.model_copy(update={"postgres_db": f"{settings.postgres_db}_test"})


@pytest.fixture(scope="session")
def migrated_database(database_settings: Settings) -> Settings:
    asyncio.run(_recreate_database(database_settings))
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_settings.database_url
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, "head")
    return database_settings


@pytest.fixture
async def db_engine(migrated_database: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database.database_url, poolclass=NullPool)
    yield engine
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
