"""Tests for GET/POST /v1/config/sources."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import regradar.core.db as db_module
from regradar.api import deps as deps_module
from regradar.api.main import create_app
from regradar.api.middleware import rate_limit as rate_limit_module
from regradar.models.enums import ApiKeyRole, FilingSource
from regradar.models.source_config import SourceConfig


def _authenticated_key_row(role: ApiKeyRole):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.organization_id = uuid.uuid4()
    row.role = role
    row.owner_label = "test-owner"
    row.rate_limit_per_minute = 1000
    row.is_active = True
    row.email = None
    row.password_hash = None
    return row


def _mock_auth_and_rate_limit(monkeypatch: pytest.MonkeyPatch, *, role: ApiKeyRole):
    row = _authenticated_key_row(role)

    mock_auth_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_auth_db.execute = AsyncMock(return_value=result)
    mock_auth_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_auth_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(deps_module, "get_session_factory", lambda: mock_session_factory)

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: mock_redis)


def _source_config_row(source: FilingSource, *, is_active: bool = True):
    row = MagicMock(spec=SourceConfig)
    row.source = source
    row.domains = ["financial"]
    row.is_active = is_active
    row.poll_interval_seconds = 300
    row.last_polled_at = None
    row.feed_url = None
    return row


def _mock_config_db(monkeypatch: pytest.MonkeyPatch, *, existing_rows: list):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=existing_rows)))
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.parametrize(
    "role",
    [
        ApiKeyRole.ADMIN,
        ApiKeyRole.ANALYST,
        ApiKeyRole.EXECUTIVE,
        ApiKeyRole.LEGAL_COUNSEL,
        ApiKeyRole.ENG_LEAD,
    ],
)
def test_get_source_config_is_readable_by_every_role(monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_config_db(monkeypatch, existing_rows=[_source_config_row(FilingSource.SEC, is_active=True)])

    response = TestClient(create_app()).get(
        "/v1/config/sources",
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    by_source = {row["source"]: row for row in body}
    assert set(by_source.keys()) == {"SEC", "FDA", "FINRA"}
    assert by_source["SEC"]["is_active"] is True
    assert by_source["SEC"]["domains"] == ["financial"]
    assert by_source["FDA"]["is_active"] is False
    assert by_source["FDA"]["domains"] == []


@pytest.mark.parametrize(
    "role", [ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD]
)
def test_update_source_config_returns_403_for_non_admin_roles(
    monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole
):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC"], "domains": ["financial"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_update_source_config_returns_422_for_invalid_source(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC", "NOTAREALSOURCE"], "domains": []},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_sources"
    assert "NOTAREALSOURCE" in body["error"]["message"]


def test_update_source_config_activates_included_and_deactivates_omitted(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    existing_fda_row = _source_config_row(FilingSource.FDA, is_active=True)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[existing_fda_row])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["SEC"], "domains": ["financial"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    by_source = {row["source"]: row for row in body}
    assert by_source["SEC"]["is_active"] is True
    assert by_source["SEC"]["domains"] == ["financial"]
    assert by_source["FDA"]["is_active"] is False
    assert existing_fda_row.is_active is False
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


def test_update_source_config_sets_fda_feed_url_on_new_row(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={
            "sources": ["FDA"],
            "domains": ["clinical"],
            "fda_feed_url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml",
        },
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    by_source = {row["source"]: row for row in body}
    assert (
        by_source["FDA"]["feed_url"]
        == "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"
    )
    mock_db.commit.assert_awaited_once()


def test_update_source_config_updates_fda_feed_url_on_existing_row(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    existing_fda_row = _source_config_row(FilingSource.FDA, is_active=True)
    existing_fda_row.feed_url = "https://old-feed.example.com/rss.xml"
    mock_db = _mock_config_db(monkeypatch, existing_rows=[existing_fda_row])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={
            "sources": ["FDA"],
            "domains": ["clinical"],
            "fda_feed_url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml",
        },
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert existing_fda_row.feed_url == (
        "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml"
    )


def test_update_source_config_ignores_fda_feed_url_for_other_sources(
    monkeypatch: pytest.MonkeyPatch,
):
    """A feed_url is FDA-connector-specific — sending fda_feed_url while
    requesting only SEC must never leak onto the (unrelated) SEC row."""
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={
            "sources": ["SEC"],
            "domains": ["financial"],
            "fda_feed_url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml",
        },
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    by_source = {row["source"]: row for row in response.json()}
    assert by_source["SEC"]["feed_url"] is None
    mock_db.commit.assert_awaited_once()


def test_update_source_config_rejects_non_url_fda_feed_url(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": ["FDA"], "domains": [], "fda_feed_url": "not-a-url"},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422


def test_update_source_config_with_no_prior_rows_and_empty_sources_deactivates_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    mock_db = _mock_config_db(monkeypatch, existing_rows=[])

    response = TestClient(create_app()).post(
        "/v1/config/sources",
        json={"sources": [], "domains": []},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    assert response.json() == []
    mock_db.add.assert_not_called()


def _mock_poll_config_db(monkeypatch: pytest.MonkeyPatch, *, row, pending_filing_ids: list | None = None):
    mock_db = AsyncMock()
    result = MagicMock()
    # Same mock `result` object answers both db.execute() calls the route
    # makes: the SourceConfig lookup (via scalar_one_or_none) and
    # _pending_filing_ids_for_org's Filing.id query (via scalars().all()).
    result.scalar_one_or_none = MagicMock(return_value=row)
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=pending_filing_ids or [])))
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.refresh = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(db_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


@pytest.mark.parametrize(
    "role", [ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD]
)
def test_poll_source_now_rejects_non_admin(monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_poll_config_db(monkeypatch, row=_source_config_row(FilingSource.SEC))

    response = TestClient(create_app()).post(
        "/v1/config/sources/SEC/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_poll_source_now_returns_404_when_not_configured(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_poll_config_db(monkeypatch, row=None)

    response = TestClient(create_app()).post(
        "/v1/config/sources/SEC/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "source_not_configured"


def test_poll_source_now_returns_409_for_fda_without_feed_url(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    fda_row = _source_config_row(FilingSource.FDA)
    fda_row.feed_url = None
    _mock_poll_config_db(monkeypatch, row=fda_row)

    response = TestClient(create_app()).post(
        "/v1/config/sources/FDA/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "feed_url_missing"


def test_poll_source_now_runs_connector_and_returns_new_filing_count(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    fda_row = _source_config_row(FilingSource.FDA)
    fda_row.feed_url = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"
    polled_at = datetime(2026, 1, 1, tzinfo=UTC)
    fda_row.last_polled_at = polled_at
    mock_db = _mock_poll_config_db(monkeypatch, row=fda_row)

    import regradar.ingestion.flows as flows_module

    mock_poll_source = AsyncMock(return_value=[MagicMock(), MagicMock()])
    monkeypatch.setattr(flows_module, "poll_source", mock_poll_source)

    response = TestClient(create_app()).post(
        "/v1/config/sources/FDA/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "FDA"
    assert body["new_filing_count"] == 2
    assert body["processing_filing_ids"] == []
    mock_poll_source.assert_awaited_once_with(fda_row.id, FilingSource.FDA)
    mock_db.refresh.assert_awaited_once_with(fda_row)


def test_poll_source_now_does_not_require_feed_url_for_sec(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    sec_row = _source_config_row(FilingSource.SEC)
    mock_db = _mock_poll_config_db(monkeypatch, row=sec_row)

    import regradar.ingestion.flows as flows_module

    monkeypatch.setattr(flows_module, "poll_source", AsyncMock(return_value=[]))

    response = TestClient(create_app()).post(
        "/v1/config/sources/SEC/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()["new_filing_count"] == 0
    mock_db.refresh.assert_awaited_once()


def test_poll_source_now_processes_pending_filings_in_background(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    sec_row = _source_config_row(FilingSource.SEC)
    pending_id = uuid.uuid4()
    _mock_poll_config_db(monkeypatch, row=sec_row, pending_filing_ids=[pending_id])

    import regradar.ingestion.flows as flows_module
    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(flows_module, "poll_source", AsyncMock(return_value=[]))
    mock_run_pipeline = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "_run_pipeline_for_filing", mock_run_pipeline)

    response = TestClient(create_app()).post(
        "/v1/config/sources/SEC/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    assert response.json()["processing_filing_ids"] == [str(pending_id)]
    # TestClient runs BackgroundTasks after the response is built — by the
    # time we get here, the background processing has already run.
    mock_run_pipeline.assert_awaited_once_with(str(pending_id))


def test_poll_source_now_marks_filing_failed_when_background_processing_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    sec_row = _source_config_row(FilingSource.SEC)
    pending_id = uuid.uuid4()
    _mock_poll_config_db(monkeypatch, row=sec_row, pending_filing_ids=[pending_id])

    import regradar.ingestion.flows as flows_module
    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(flows_module, "poll_source", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        pipeline_tasks_module,
        "_run_pipeline_for_filing",
        AsyncMock(side_effect=RuntimeError("LLM call failed")),
    )
    mock_mark_failed = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "_mark_filing_failed", mock_mark_failed)

    response = TestClient(create_app()).post(
        "/v1/config/sources/SEC/poll", headers={"Authorization": "Bearer rr_test-key"}
    )

    assert response.status_code == 200
    mock_mark_failed.assert_awaited_once_with(str(pending_id), "LLM call failed")


@pytest.mark.parametrize(
    "role", [ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD]
)
def test_fetch_now_rejects_non_admin(monkeypatch: pytest.MonkeyPatch, role: ApiKeyRole):
    _mock_auth_and_rate_limit(monkeypatch, role=role)
    _mock_poll_config_db(monkeypatch, row=_source_config_row(FilingSource.SEC))

    response = TestClient(create_app()).post(
        "/v1/config/sources/fetch-now",
        json={"sources": ["SEC"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_fetch_now_returns_422_for_invalid_source(monkeypatch: pytest.MonkeyPatch):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_poll_config_db(monkeypatch, row=_source_config_row(FilingSource.SEC))

    response = TestClient(create_app()).post(
        "/v1/config/sources/fetch-now",
        json={"sources": ["SEC", "NOTAREALSOURCE"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sources"


def test_fetch_now_returns_404_when_a_requested_source_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    _mock_poll_config_db(monkeypatch, row=None)

    response = TestClient(create_app()).post(
        "/v1/config/sources/fetch-now",
        json={"sources": ["SEC"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "source_not_configured"


def test_fetch_now_polls_every_requested_source_and_processes_pending_filings(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_auth_and_rate_limit(monkeypatch, role=ApiKeyRole.ADMIN)
    sec_row = _source_config_row(FilingSource.SEC)
    pending_id = uuid.uuid4()
    _mock_poll_config_db(monkeypatch, row=sec_row, pending_filing_ids=[pending_id])

    import regradar.ingestion.flows as flows_module
    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    mock_poll_source = AsyncMock(return_value=[MagicMock()])
    monkeypatch.setattr(flows_module, "poll_source", mock_poll_source)
    mock_run_pipeline = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "_run_pipeline_for_filing", mock_run_pipeline)

    response = TestClient(create_app()).post(
        "/v1/config/sources/fetch-now",
        json={"sources": ["SEC", "FINRA"]},
        headers={"Authorization": "Bearer rr_test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    sources_polled = {result["source"] for result in body["results"]}
    assert sources_polled == {"SEC", "FINRA"}
    assert all(result["new_filing_count"] == 1 for result in body["results"])
    assert body["processing_filing_ids"] == [str(pending_id)]
    assert mock_poll_source.await_count == 2
    mock_run_pipeline.assert_awaited_once_with(str(pending_id))
