import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEY_PEPPER", "test-pepper")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")

from collections.abc import Iterator  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

import pytest  # noqa: E402
import redis  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api import quotes  # noqa: E402
from app.api.admin import router as admin_router  # noqa: E402
from app.core.admission import get_limiter  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.errors import register_error_handlers  # noqa: E402
from app.data.db import get_db  # noqa: E402
from app.data.redis_client import RedisClient  # noqa: E402
from app.models import Base  # noqa: E402
from tests.fakes import AllowAllLimiter  # noqa: E402

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
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    application = FastAPI()
    register_error_handlers(application)
    application.include_router(admin_router)
    application.include_router(quotes.router)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_limiter] = lambda: AllowAllLimiter()
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.admin_token}"}


@pytest.fixture
def redis_client() -> Iterator[RedisClient]:
    """Real Redis on database 15 (never db 0, which holds live compose data)."""
    url = (
        os.environ.get("TEST_REDIS_URL")
        or urlsplit(settings.redis_url)._replace(path="/15").geturl()
    )
    rc: RedisClient = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.5)
    try:
        rc.ping()
    except redis.RedisError:
        pytest.skip(f"Redis not reachable at {url}")
    if rc.connection_pool.connection_kwargs.get("db", 0) == 0:
        pytest.skip("refusing to flush database 0; set TEST_REDIS_URL to a /15 URL")
    rc.flushdb()
    yield rc
    rc.flushdb()
    rc.close()


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
