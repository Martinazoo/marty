import os

import pytest

from app.core.config import Settings


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
