import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.admin import router as admin_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.keys import hash_secret, parse_key, verify_key
from app.data.db import get_db
from app.models import ApiKey, Base, Org

VALID_PLAN: dict[str, Any] = {
    "name": "pro",
    "rate_limit_per_second": 20,
    "burst": 40,
    "monthly_quota": 100_000,
}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(admin_router)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.admin_token}"}


def make_plan(client: TestClient, auth: dict[str, str], **overrides: Any) -> dict[str, Any]:
    resp = client.post("/admin/plans", headers=auth, json={**VALID_PLAN, **overrides})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def make_org(
    client: TestClient, auth: dict[str, str], plan_id: str, name: str = "acme"
) -> dict[str, Any]:
    resp = client.post("/admin/orgs", headers=auth, json={"name": name, "plan_id": plan_id})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


# --- admin auth (T-11) -------------------------------------------------------

ADMIN_ROUTES = [
    ("POST", "/admin/plans"),
    ("GET", "/admin/plans"),
    ("POST", "/admin/orgs"),
    ("GET", "/admin/orgs"),
    ("POST", f"/admin/orgs/{UNKNOWN_ID}/keys"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_reject_missing_token(client: TestClient, method: str, path: str) -> None:
    resp = client.request(method, path, json={})
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_reject_wrong_token(client: TestClient, method: str, path: str) -> None:
    resp = client.request(method, path, json={}, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_reject_api_keys_with_403(client: TestClient, method: str, path: str) -> None:
    resp = client.request(method, path, json={}, headers={"Authorization": "Bearer qk_abc_def"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


def test_empty_admin_token_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_token", "")
    resp = client.get("/admin/plans", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 401


# --- plans -------------------------------------------------------------------


def test_create_plan(client: TestClient, auth: dict[str, str]) -> None:
    plan = make_plan(client, auth)
    assert plan["name"] == "pro"
    assert plan["burst"] == 40
    assert uuid.UUID(plan["id"])


def test_duplicate_plan_name_conflicts(client: TestClient, auth: dict[str, str]) -> None:
    make_plan(client, auth)
    resp = client.post("/admin/plans", headers=auth, json=VALID_PLAN)
    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_name_taken"


@pytest.mark.parametrize(
    "override",
    [
        {"rate_limit_per_second": 0},
        {"burst": 10},  # below the rate of 20
        {"monthly_quota": 0},
        {"monthly_quota": 2_147_483_648},  # overflows a 32-bit column
        {"name": "   "},
        {"burst": "lots"},
        {"unexpected": 1},
    ],
)
def test_invalid_plan_is_rejected(
    client: TestClient, auth: dict[str, str], override: dict[str, Any]
) -> None:
    resp = client.post("/admin/plans", headers=auth, json={**VALID_PLAN, **override})
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"


def test_list_plans_paginates(client: TestClient, auth: dict[str, str]) -> None:
    for name in ("free", "pro", "business"):
        make_plan(client, auth, name=name)
    first = client.get("/admin/plans?limit=2", headers=auth).json()
    rest = client.get("/admin/plans?limit=2&offset=2", headers=auth).json()
    assert len(first) == 2
    assert len(rest) == 1
    assert {p["id"] for p in first}.isdisjoint({p["id"] for p in rest})


# --- orgs --------------------------------------------------------------------


def test_create_and_list_orgs(client: TestClient, auth: dict[str, str]) -> None:
    plan = make_plan(client, auth)
    org = make_org(client, auth, plan["id"])
    assert org["plan_id"] == plan["id"]
    assert org["is_active"] is True
    listed = client.get("/admin/orgs", headers=auth).json()
    assert [o["id"] for o in listed] == [org["id"]]


def test_org_with_unknown_plan_is_404(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.post("/admin/orgs", headers=auth, json={"name": "acme", "plan_id": UNKNOWN_ID})
    assert resp.status_code == 404
    assert resp.json()["error"] == "plan_not_found"


def test_duplicate_org_name_conflicts(client: TestClient, auth: dict[str, str]) -> None:
    plan = make_plan(client, auth)
    make_org(client, auth, plan["id"])
    resp = client.post("/admin/orgs", headers=auth, json={"name": "acme", "plan_id": plan["id"]})
    assert resp.status_code == 409
    assert resp.json()["error"] == "org_name_taken"


# --- key creation (G-2) ------------------------------------------------------


def test_create_key_returns_plaintext_once(client: TestClient, auth: dict[str, str]) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    resp = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth, json={"label": "ci"})
    assert resp.status_code == 201
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    parsed = parse_key(body["api_key"])
    assert parsed is not None
    assert parsed[0] == body["prefix"]
    assert body["org_id"] == org["id"]
    assert body["label"] == "ci"
    assert "key_hash" not in body


def test_create_key_without_body(client: TestClient, auth: dict[str, str]) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    resp = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth)
    assert resp.status_code == 201
    assert resp.json()["label"] is None


def test_database_never_holds_the_secret(
    client: TestClient, auth: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    created = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth, json={}).json()
    parsed = parse_key(created["api_key"])
    assert parsed is not None
    _, secret = parsed

    with session_factory() as session:
        dump = " ".join(str(row) for row in session.execute(text("SELECT * FROM api_keys")))
        row = session.scalars(select(ApiKey)).one()
        assert row.key_hash == hash_secret(secret)
        assert verify_key(created["api_key"], row.key_hash)

    assert secret not in dump
    assert created["api_key"] not in dump


def test_keys_are_unique(client: TestClient, auth: dict[str, str]) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    url = f"/admin/orgs/{org['id']}/keys"
    first = client.post(url, headers=auth).json()
    second = client.post(url, headers=auth).json()
    assert first["api_key"] != second["api_key"]
    assert first["prefix"] != second["prefix"]


def test_create_key_for_unknown_org_is_404(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.post(f"/admin/orgs/{UNKNOWN_ID}/keys", headers=auth)
    assert resp.status_code == 404
    assert resp.json()["error"] == "org_not_found"


def test_create_key_for_inactive_org_conflicts(
    client: TestClient, auth: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    with session_factory() as session:
        row = session.get(Org, uuid.UUID(org["id"]))
        assert row is not None
        row.is_active = False
        session.commit()
    resp = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth)
    assert resp.status_code == 409
    assert resp.json()["error"] == "org_inactive"


def test_label_too_long_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    org = make_org(client, auth, make_plan(client, auth)["id"])
    resp = client.post(f"/admin/orgs/{org['id']}/keys", headers=auth, json={"label": "x" * 129})
    assert resp.status_code == 422
