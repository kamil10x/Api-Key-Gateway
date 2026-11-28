import uuid
from datetime import UTC, datetime

import pytest
import redis

from app.core.limiter import (
    Limiter,
    LimiterUnavailableError,
    Limits,
    month_label,
    next_month_start,
)


def test_month_label() -> None:
    assert month_label(datetime(2026, 11, 28, tzinfo=UTC)) == "202611"


def test_next_month_start_rolls_year() -> None:
    assert next_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )


def test_next_month_start_mid_year() -> None:
    assert next_month_start(datetime(2026, 6, 15, tzinfo=UTC)) == datetime(2026, 7, 1, tzinfo=UTC)


def test_unreachable_redis_raises_limiter_unavailable() -> None:
    dead = redis.Redis(host="127.0.0.1", port=1, decode_responses=True, socket_connect_timeout=0.2)
    limiter = Limiter(dead)
    with pytest.raises(LimiterUnavailableError):
        limiter.admit(
            org_id=uuid.uuid4(),
            key_id=uuid.uuid4(),
            limits=Limits(10, 10, 100),
            request_id="r",
            use_idem=False,
        )
