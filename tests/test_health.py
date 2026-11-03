import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.main import create_app
from tests.conftest import make_settings


def test_liveness():
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_readiness_reports_unreachable_database():
    settings = make_settings(postgres_host="127.0.0.1", postgres_port=1)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}


def test_api_docs_disabled_in_production():
    with TestClient(create_app(make_settings(app_env="production"))) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


@pytest.mark.integration
def test_readiness_with_real_database():
    with TestClient(create_app(Settings(app_env="test"))) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"
