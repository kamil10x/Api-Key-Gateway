import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.limiter import (
    Admission,
    LimiterUnavailableError,
    Limits,
    month_label,
    next_month_start,
)


def make_admission(**overrides: Any) -> Admission:
    base: dict[str, Any] = {
        "allowed": True,
        "reason": "ok",
        "remaining": 99,
        "retry_after_s": 0,
        "stream_id": "1-0",
        "month": "202611",
        "quota_reset_at": datetime(2026, 12, 1, tzinfo=UTC),
        "retry_at": datetime(2026, 11, 28, 12, 0, 2, tzinfo=UTC),
    }
    return Admission(**{**base, **overrides})


class AllowAllLimiter:
    """Default in unit tests so they need no Redis."""

    def admit(
        self,
        *,
        org_id: uuid.UUID,
        key_id: uuid.UUID,
        limits: Limits,
        request_id: str,
        use_idem: bool,
    ) -> Admission:
        now = datetime.now(UTC)
        return make_admission(
            remaining=limits.monthly_quota - 1,
            month=month_label(now),
            quota_reset_at=next_month_start(now),
            retry_at=now,
        )

    def void(self, admission: Admission, *, org_id: uuid.UUID, request_id: str) -> bool:
        return False


class ScriptedLimiter:
    def __init__(self, admission: Admission) -> None:
        self.admission = admission
        self.calls = 0

    def admit(
        self,
        *,
        org_id: uuid.UUID,
        key_id: uuid.UUID,
        limits: Limits,
        request_id: str,
        use_idem: bool,
    ) -> Admission:
        self.calls += 1
        return self.admission

    def void(self, admission: Admission, *, org_id: uuid.UUID, request_id: str) -> bool:
        return False


class UnavailableLimiter:
    def admit(
        self,
        *,
        org_id: uuid.UUID,
        key_id: uuid.UUID,
        limits: Limits,
        request_id: str,
        use_idem: bool,
    ) -> Admission:
        raise LimiterUnavailableError

    def void(self, admission: Admission, *, org_id: uuid.UUID, request_id: str) -> bool:
        raise LimiterUnavailableError
