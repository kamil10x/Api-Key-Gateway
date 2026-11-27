import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.keys import hash_secret, parse_key, verify_key
from app.data.db import get_db
from app.models import ApiKey, Org

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class AuthContext:
    org_id: uuid.UUID
    key_id: uuid.UUID
    plan_id: uuid.UUID


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_secret("timing-equaliser")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite returns naive datetimes


def _invalid() -> UnauthorizedError:
    # Same body for every failure so callers can't tell which part was wrong.
    return UnauthorizedError(
        "invalid_api_key",
        "Missing or invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_api_key(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    # BASELINE (D12): two DB queries per request on purpose; replaced by cache later.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _invalid()
    raw = authorization[7:].strip()
    parsed = parse_key(raw)
    if parsed is None:
        raise _invalid()
    prefix, _ = parsed

    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        verify_key(raw, _dummy_hash())  # burn equal HMAC time for unknown prefixes
        raise _invalid()
    if not verify_key(raw, key.key_hash):
        raise _invalid()

    now = datetime.now(UTC)
    if key.revoked_at is not None and _aware(key.revoked_at) <= now:
        raise _invalid()
    if key.expires_at is not None and _aware(key.expires_at) <= now:
        raise _invalid()

    org = db.get(Org, key.org_id)
    if org is None or not org.is_active:
        raise ForbiddenError("org_inactive", "Organization is disabled")

    return AuthContext(org_id=org.id, key_id=key.id, plan_id=org.plan_id)
