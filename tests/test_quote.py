import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import ApiKey, Org
from tests.helpers import make_org, make_plan


@pytest.fixture
def issued(client: TestClient, auth: dict[str, str]) -> dict[str, str]:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    body = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth).json()
    return {"org_id": org["id"], "api_key": body["api_key"], "prefix": body["prefix"]}


def call(client: TestClient, key: str | None) -> "TestClient.get":  # type: ignore[name-defined]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return client.get("/v1/quote", headers=headers)


def test_valid_key_gets_a_quote(client: TestClient, issued: dict[str, str]) -> None:
    resp = call(client, issued["api_key"])
    assert resp.status_code == 200
    assert {"text", "author"} <= resp.json().keys()


def test_missing_header_is_401(client: TestClient) -> None:
    assert call(client, None).status_code == 401


@pytest.mark.parametrize("bad", ["garbage", "qk_only", "qk__x", "xx_abc_def"])
def test_malformed_key_is_401(client: TestClient, bad: str) -> None:
    assert call(client, bad).status_code == 401


def test_wrong_secret_and_unknown_prefix_look_identical(
    client: TestClient, issued: dict[str, str]
) -> None:
    wrong_secret = call(client, f"qk_{issued['prefix']}_nottherealsecret")
    unknown_prefix = call(client, "qk_ffffffffffff_nottherealsecret")
    assert wrong_secret.status_code == unknown_prefix.status_code == 401
    assert wrong_secret.json() == unknown_prefix.json()


def _set_key(sf: sessionmaker[Session], prefix: str, **fields: datetime) -> None:
    with sf() as s:
        row = s.scalars(select(ApiKey).where(ApiKey.prefix == prefix)).one()
        for k, v in fields.items():
            setattr(row, k, v)
        s.commit()


def test_revoked_key_is_401(
    client: TestClient, issued: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    _set_key(session_factory, issued["prefix"], revoked_at=datetime.now(UTC) - timedelta(seconds=1))
    assert call(client, issued["api_key"]).status_code == 401


def test_expired_key_is_401(
    client: TestClient, issued: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    _set_key(session_factory, issued["prefix"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert call(client, issued["api_key"]).status_code == 401


def test_future_expiry_still_works(
    client: TestClient, issued: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    _set_key(session_factory, issued["prefix"], expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert call(client, issued["api_key"]).status_code == 200


def test_inactive_org_is_403(
    client: TestClient, issued: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as s:
        org = s.get(Org, uuid.UUID(issued["org_id"]))
        assert org is not None
        org.is_active = False
        s.commit()
    resp = call(client, issued["api_key"])
    assert resp.status_code == 403
    assert resp.json()["error"] == "org_inactive"


def test_admin_token_cannot_call_quote(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/v1/quote", headers=auth)
    assert resp.status_code == 401
