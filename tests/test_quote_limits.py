from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.admission import get_limiter
from app.core.limiter import Limiter
from app.data.redis_client import RedisClient
from tests.conftest import make_org, make_plan
from tests.fakes import ScriptedLimiter, UnavailableLimiter, make_admission


def _issue_key(client: TestClient, auth: dict[str, str], **plan: Any) -> dict[str, str]:
    p = make_plan(client, auth, **plan)
    org = make_org(client, auth, p["id"])
    # Copy the payload/response shape from your existing test_quote.py if this differs.
    resp = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth, json={"label": "test"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_key']}"}


def test_success_carries_quota_headers(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/v1/quote", headers=_issue_key(client, auth))
    assert resp.status_code == 200
    assert resp.headers["X-Quota-Limit"] == "100000"
    assert resp.headers["X-Quota-Remaining"] == "99999"
    assert resp.headers["X-Quota-Reset"].isdigit()


def test_rate_limited_response(client: TestClient, auth: dict[str, str], app: FastAPI) -> None:
    scripted = ScriptedLimiter(
        make_admission(allowed=False, reason="rate_limited", retry_after_s=2, remaining=0)
    )
    app.dependency_overrides[get_limiter] = lambda: scripted
    resp = client.get("/v1/quote", headers=_issue_key(client, auth))
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "2"
    body = resp.json()
    assert body["error"] == "rate_limited"
    assert body["retry_after_seconds"] == 2
    assert body["limit"] == 20
    assert body["remaining"] == 0
    assert body["reset_at"].endswith("Z")


def test_quota_exceeded_response(client: TestClient, auth: dict[str, str], app: FastAPI) -> None:
    scripted = ScriptedLimiter(
        make_admission(allowed=False, reason="quota_exceeded", retry_after_s=936000, remaining=0)
    )
    app.dependency_overrides[get_limiter] = lambda: scripted
    resp = client.get("/v1/quote", headers=_issue_key(client, auth))
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "936000"
    body = resp.json()
    assert body["error"] == "quota_exceeded"
    assert body["retry_after_seconds"] == 936000
    assert body["limit"] == 100_000
    assert body["reset_at"] == "2026-12-01T00:00:00Z"


def test_redis_down_fails_closed(client: TestClient, auth: dict[str, str], app: FastAPI) -> None:
    app.dependency_overrides[get_limiter] = lambda: UnavailableLimiter()
    resp = client.get("/v1/quote", headers=_issue_key(client, auth))
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"
    assert resp.json()["error"] == "service_unavailable"
    assert "text" not in resp.json()


def test_invalid_key_never_reaches_limiter(client: TestClient, app: FastAPI) -> None:
    scripted = ScriptedLimiter(make_admission())
    app.dependency_overrides[get_limiter] = lambda: scripted
    resp = client.get("/v1/quote", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert scripted.calls == 0


@pytest.mark.integration
def test_burst_then_429(
    client: TestClient, auth: dict[str, str], app: FastAPI, redis_client: RedisClient
) -> None:
    limiter = Limiter(redis_client)
    app.dependency_overrides[get_limiter] = lambda: limiter
    headers = _issue_key(client, auth, rate_limit_per_second=1, burst=2)
    codes = [client.get("/v1/quote", headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    last = client.get("/v1/quote", headers=headers)
    assert last.json()["error"] == "rate_limited"
    assert last.headers["Retry-After"] == "1"


@pytest.mark.integration
def test_monthly_quota_exhausted(
    client: TestClient, auth: dict[str, str], app: FastAPI, redis_client: RedisClient
) -> None:
    limiter = Limiter(redis_client)
    app.dependency_overrides[get_limiter] = lambda: limiter
    headers = _issue_key(client, auth, monthly_quota=3)
    ok = [client.get("/v1/quote", headers=headers) for _ in range(3)]
    assert [r.status_code for r in ok] == [200, 200, 200]
    assert [r.headers["X-Quota-Remaining"] for r in ok] == ["2", "1", "0"]
    denied = client.get("/v1/quote", headers=headers)
    assert denied.status_code == 429
    assert denied.json()["error"] == "quota_exceeded"
    assert int(denied.headers["Retry-After"]) == denied.json()["retry_after_seconds"] > 0


@pytest.mark.integration
def test_same_request_id_counted_once(
    client: TestClient, auth: dict[str, str], app: FastAPI, redis_client: RedisClient
) -> None:
    limiter = Limiter(redis_client)
    app.dependency_overrides[get_limiter] = lambda: limiter
    headers = {**_issue_key(client, auth, monthly_quota=100), "X-Request-ID": "req-abcdef12"}
    first = client.get("/v1/quote", headers=headers)
    second = client.get("/v1/quote", headers=headers)
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.headers["X-Quota-Remaining"] == second.headers["X-Quota-Remaining"] == "99"
