"""Atomic admission control: token bucket + monthly quota in one Redis script (D-05, D-06)."""

import logging
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import redis

from app.data.redis_client import RedisClient

log = logging.getLogger(__name__)

STREAM_KEY = "usage:events"
_LUA_DIR = Path(__file__).parent / "lua"
_COUNTER_TTL_EXTRA = timedelta(days=7)


@dataclass(frozen=True)
class Limits:
    rate_per_second: float
    burst: int
    monthly_quota: int


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str  # ok | replay | rate_limited | quota_exceeded
    remaining: int  # monthly quota left (meaningful when allowed)
    retry_after_s: int  # 0 when allowed
    stream_id: str  # '' for replays and rejections
    month: str  # YYYYMM of the counter used (needed by void)
    quota_reset_at: datetime
    retry_at: datetime


class LimiterUnavailableError(Exception):
    """Redis could not decide: the caller must fail closed (D-12)."""


class AdmissionController(Protocol):
    def admit(
        self,
        *,
        org_id: uuid.UUID,
        key_id: uuid.UUID,
        limits: Limits,
        request_id: str,
        use_idem: bool,
    ) -> Admission: ...

    def void(self, admission: Admission, *, org_id: uuid.UUID, request_id: str) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def month_label(now: datetime) -> str:
    return now.strftime("%Y%m")


def next_month_start(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def _tag(org_id: uuid.UUID) -> str:
    return f"{{org:{org_id}}}"  # hash tag keeps one org's keys in one Cluster slot


class Limiter:
    def __init__(
        self,
        client: RedisClient,
        *,
        clock: Callable[[], datetime] = _utcnow,
        marker_ttl_seconds: int = 60,
    ) -> None:
        self._clock = clock
        self._marker_ttl = marker_ttl_seconds
        # Script objects use EVALSHA and reload on NOSCRIPT (e.g. after a Redis restart).
        self._admit = client.register_script((_LUA_DIR / "admit.lua").read_text())
        self._void = client.register_script((_LUA_DIR / "void.lua").read_text())

    def admit(
        self,
        *,
        org_id: uuid.UUID,
        key_id: uuid.UUID,
        limits: Limits,
        request_id: str,
        use_idem: bool,
    ) -> Admission:
        now = self._clock()
        month = month_label(now)
        reset_at = next_month_start(now)
        ttl = int((reset_at - now + _COUNTER_TTL_EXTRA).total_seconds())
        tag = _tag(org_id)
        keys = [f"{tag}:bucket", f"{tag}:q:{month}", f"{tag}:idem:{request_id}", STREAM_KEY]
        args: list[str | int | float] = [
            limits.rate_per_second,
            limits.burst,
            limits.monthly_quota,
            ttl,
            self._marker_ttl,
            request_id,
            str(org_id),
            str(key_id),
            "1" if use_idem else "0",
        ]
        try:
            out = self._admit(keys=keys, args=args)
        except redis.RedisError as exc:
            log.warning("limiter unavailable: %s", type(exc).__name__)
            raise LimiterUnavailableError from exc

        allowed = int(out[0]) == 1
        reason = str(out[1])
        if reason == "rate_limited":
            retry_s = max(1, math.ceil(int(out[3]) / 1000))
        elif reason == "quota_exceeded":
            retry_s = max(1, math.ceil((reset_at - now).total_seconds()))
        else:
            retry_s = 0
        return Admission(
            allowed=allowed,
            reason=reason,
            remaining=int(out[2]),
            retry_after_s=retry_s,
            stream_id=str(out[4]),
            month=month,
            quota_reset_at=reset_at,
            retry_at=now + timedelta(seconds=retry_s),
        )

    def void(self, admission: Admission, *, org_id: uuid.UUID, request_id: str) -> bool:
        """Reverse one admission (handler did not return 2xx). True only the first time."""
        if admission.reason != "ok":
            return False
        tag = _tag(org_id)
        keys = [
            f"{tag}:q:{admission.month}",
            f"{tag}:void:{admission.stream_id}",
            f"{tag}:idem:{request_id}",
            STREAM_KEY,
        ]
        try:
            out = self._void(keys=keys, args=[admission.stream_id, str(org_id), request_id])
        except redis.RedisError as exc:
            log.warning("limiter void failed: %s", type(exc).__name__)
            raise LimiterUnavailableError from exc
        return int(out) == 1
