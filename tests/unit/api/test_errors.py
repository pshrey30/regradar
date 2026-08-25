"""Tests for the shared API error envelope."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from regradar.api.errors import ApiError, register_error_handlers
from regradar.api.middleware.request_id import RequestIdMiddleware


class _Payload(BaseModel):
    count: int


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise ApiError(status_code=401, code="invalid_api_key", message="bad key")

    @app.get("/boom-with-headers")
    async def boom_with_headers():
        raise ApiError(
            status_code=429,
            code="rate_limit_exceeded",
            message="slow down",
            headers={"Retry-After": "42"},
        )

    @app.get("/needs-int")
    async def needs_int(count: int):
        return {"count": count}

    @app.post("/needs-payload")
    async def needs_payload(payload: _Payload):
        return payload

    return app


def test_api_error_renders_envelope():
    response = TestClient(_make_app()).get("/boom")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_api_key"
    assert body["error"]["message"] == "bad key"


def test_api_error_includes_request_id_matching_header():
    response = TestClient(_make_app()).get("/boom")

    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_api_error_includes_custom_headers():
    response = TestClient(_make_app()).get("/boom-with-headers")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


def test_invalid_query_param_type_renders_shared_envelope():
    response = TestClient(_make_app()).get("/needs-int", params={"count": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "detail" not in body


def test_invalid_query_param_includes_request_id():
    response = TestClient(_make_app()).get("/needs-int", params={"count": "not-a-number"})

    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_missing_required_body_field_renders_shared_envelope():
    response = TestClient(_make_app()).post("/needs-payload", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
