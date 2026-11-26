import uuid
from datetime import UTC, datetime, timedelta

from app.models import ApiKey, Base


def _key(
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> ApiKey:
    return ApiKey(
        org_id=uuid.uuid4(),
        prefix="qk_test",
        key_hash="0" * 64,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


def test_tables_registered() -> None:
    assert set(Base.metadata.tables) == {"plans", "orgs", "api_keys"}


def test_api_keys_never_store_plaintext_secret() -> None:
    cols = set(Base.metadata.tables["api_keys"].columns.keys())
    assert {"prefix", "key_hash"} <= cols
    assert cols.isdisjoint({"secret", "key", "api_key", "plaintext"})


def test_key_without_expiry_or_revocation_is_usable() -> None:
    assert _key().is_usable(datetime.now(UTC))


def test_revoked_key_is_not_usable() -> None:
    now = datetime.now(UTC)
    assert not _key(revoked_at=now - timedelta(seconds=1)).is_usable(now)


def test_key_inside_rotation_grace_is_usable() -> None:
    now = datetime.now(UTC)
    assert _key(expires_at=now + timedelta(seconds=30)).is_usable(now)


def test_key_past_grace_is_not_usable() -> None:
    now = datetime.now(UTC)
    assert not _key(expires_at=now - timedelta(seconds=1)).is_usable(now)
