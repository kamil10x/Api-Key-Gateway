import re
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, Response

from app.core.api_auth import AuthContext, authenticate_api_key
from app.core.config import settings
from app.core.errors import ServiceUnavailableError, TooManyRequestsError
from app.core.limiter import Admission, AdmissionController, Limiter, LimiterUnavailableError
from app.data.redis_client import get_redis

# nginx's $request_id is 32 hex chars; anything else is treated as "no id".
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


@lru_cache(maxsize=1)
def get_limiter() -> Limiter:
    return Limiter(get_redis(), marker_ttl_seconds=settings.request_marker_ttl_seconds)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def admit_request(
    ctx: Annotated[AuthContext, Depends(authenticate_api_key)],
    limiter: Annotated[AdmissionController, Depends(get_limiter)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> Admission:
    if x_request_id and _REQUEST_ID.fullmatch(x_request_id):
        request_id, use_idem = x_request_id, True  # D-14: LB-assigned id
    else:
        request_id, use_idem = uuid.uuid4().hex, False

    try:
        adm = limiter.admit(
            org_id=ctx.org_id,
            key_id=ctx.key_id,
            limits=ctx.limits,
            request_id=request_id,
            use_idem=use_idem,
        )
    except LimiterUnavailableError as exc:
        raise ServiceUnavailableError(
            "service_unavailable",
            "Rate limiter unavailable, retry shortly",
            headers={"Retry-After": "1"},
        ) from exc

    if not adm.allowed:
        quota = adm.reason == "quota_exceeded"
        if quota:
            message = "Monthly allowance used. Resets at the start of next month (UTC)."
        else:
            message = f"Too many requests. Retry in {adm.retry_after_s} seconds."
        raise TooManyRequestsError(
            adm.reason,
            message,
            headers={"Retry-After": str(adm.retry_after_s)},
            extra={
                "retry_after_seconds": adm.retry_after_s,
                "limit": ctx.limits.monthly_quota if quota else int(ctx.limits.rate_per_second),
                "remaining": 0,
                "reset_at": _iso(adm.quota_reset_at if quota else adm.retry_at),
            },
        )

    response.headers["X-Quota-Limit"] = str(ctx.limits.monthly_quota)
    response.headers["X-Quota-Remaining"] = str(max(0, adm.remaining))
    response.headers["X-Quota-Reset"] = str(int(adm.quota_reset_at.timestamp()))
    return adm
