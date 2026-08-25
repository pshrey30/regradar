"""Tests for the FastAPI app factory: health check, request-ID middleware, docs."""

import pytest
from fastapi.testclient import TestClient

from regradar.api import main as main_module


@pytest.fixture
def app():
    return main_module.create_app()


def test_health_ok_when_db_and_redis_reachable(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", _async_return(True))
    monkeypatch.setattr(main_module, "_check_redis", _async_return(True))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


def test_health_503_when_database_unreachable(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", _async_return(False))
    monkeypatch.setattr(main_module, "_check_redis", _async_return(True))

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable", "redis": "ok"}


def test_health_503_when_redis_unreachable(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", _async_return(True))
    monkeypatch.setattr(main_module, "_check_redis", _async_return(False))

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json()["redis"] == "unreachable"


def test_response_includes_request_id_header(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", _async_return(True))
    monkeypatch.setattr(main_module, "_check_redis", _async_return(True))

    response = TestClient(app).get("/health")

    assert "x-request-id" in response.headers
    assert len(response.headers["x-request-id"]) == 36  # UUID4 string length


def test_two_requests_get_different_request_ids(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", _async_return(True))
    monkeypatch.setattr(main_module, "_check_redis", _async_return(True))

    client = TestClient(app)
    first = client.get("/health").headers["x-request-id"]
    second = client.get("/health").headers["x-request-id"]

    assert first != second


def test_openapi_docs_available(app):
    response = TestClient(app).get("/docs")

    assert response.status_code == 200


def test_openapi_schema_has_title_and_version(app):
    schema = TestClient(app).get("/openapi.json").json()

    assert schema["info"]["title"] == "RegRadar"
    assert schema["info"]["version"]


def _async_return(value: bool):
    async def _fn() -> bool:
        return value

    return _fn
