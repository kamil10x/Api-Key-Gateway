import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.core.limiter import STREAM_KEY, Admission, Limiter, Limits
from app.data.redis_client import RedisClient

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
ORG = uuid.UUID(int=1)
KEY = uuid.UUID(int=2)
COUNTER = f"{{org:{ORG}}}:q:202611"
BUCKET = f"{{org:{ORG}}}:bucket"


def _admit(
    limiter: Limiter, limits: Limits, rid: str | None = None, use_idem: bool = False
) -> Admission:
    return limiter.admit(
        org_id=ORG,
        key_id=KEY,
        limits=limits,
        request_id=rid or uuid.uuid4().hex,
        use_idem=use_idem,
    )


def _limiter(rc: RedisClient) -> Limiter:
    return Limiter(rc, clock=lambda: NOW)


def _hammer(limiter: Limiter, limits: Limits, n: int) -> list[Admission]:
    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(lambda _: _admit(limiter, limits), range(n)))


def test_burst_is_exact_under_concurrency(redis_client: RedisClient) -> None:
    results = _hammer(_limiter(redis_client), Limits(0.01, 100, 10**6), 400)
    assert sum(r.allowed for r in results) == 100
    assert redis_client.get(COUNTER) == "100"
    assert redis_client.xlen(STREAM_KEY) == 100


def test_quota_is_exact_and_checked_before_bucket(redis_client: RedisClient) -> None:
    results = _hammer(_limiter(redis_client), Limits(0.01, 1000, 50), 200)
    assert sum(r.allowed for r in results) == 50
    assert {r.reason for r in results if not r.allowed} == {"quota_exceeded"}
    tokens = float(redis_client.hget(BUCKET, "tokens") or "nan")
    assert tokens == pytest.approx(950, abs=0.5)  # rejected requests took no token


def test_rejections_change_nothing(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(0.01, 3, 100)
    assert all(_admit(limiter, limits).allowed for _ in range(3))
    assert not any(_admit(limiter, limits).allowed for _ in range(5))
    assert redis_client.get(COUNTER) == "3"
    assert redis_client.xlen(STREAM_KEY) == 3


def test_rate_limited_retry_after(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(0.5, 1, 100)
    assert _admit(limiter, limits).allowed
    denied = _admit(limiter, limits)
    assert denied.reason == "rate_limited"
    assert denied.retry_after_s == 2


def test_bucket_refills(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(20, 1, 100)
    assert _admit(limiter, limits).allowed
    assert not _admit(limiter, limits).allowed
    time.sleep(0.15)
    assert _admit(limiter, limits).allowed


def test_replay_is_free(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(10, 10, 100)
    first = _admit(limiter, limits, "req-same-0001", use_idem=True)
    second = _admit(limiter, limits, "req-same-0001", use_idem=True)
    assert (first.reason, second.reason) == ("ok", "replay")
    assert second.allowed
    assert redis_client.get(COUNTER) == "1"
    assert redis_client.xlen(STREAM_KEY) == 1


def test_void_is_idempotent_and_retry_is_fresh(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(10, 10, 100)
    adm = _admit(limiter, limits, "req-void-0001", use_idem=True)
    assert limiter.void(adm, org_id=ORG, request_id="req-void-0001") is True
    assert limiter.void(adm, org_id=ORG, request_id="req-void-0001") is False
    assert redis_client.get(COUNTER) == "0"
    retry = _admit(limiter, limits, "req-void-0001", use_idem=True)
    assert retry.reason == "ok"
    assert redis_client.get(COUNTER) == "1"


def test_month_boundary_uses_new_counter(redis_client: RedisClient) -> None:
    limits = Limits(100, 100, 1)
    nov = Limiter(redis_client, clock=lambda: datetime(2026, 11, 30, 23, 59, 50, tzinfo=UTC))
    dec = Limiter(redis_client, clock=lambda: datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC))
    assert _admit(nov, limits).allowed
    blocked = _admit(nov, limits)
    assert blocked.reason == "quota_exceeded"
    assert blocked.retry_after_s == 10
    assert _admit(dec, limits).allowed


def test_recovers_after_script_cache_flush(redis_client: RedisClient) -> None:
    limiter = _limiter(redis_client)
    limits = Limits(10, 10, 100)
    assert _admit(limiter, limits).allowed
    redis_client.script_flush()  # what a Redis restart does to the script cache
    assert _admit(limiter, limits).allowed
