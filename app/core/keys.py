from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.core.config import settings

KEY_TAG = "qk"
PREFIX_BYTES = 6  # public lookup id, stored in plain text
SECRET_BYTES = 32  # 256 bits of entropy


@dataclass(frozen=True)
class GeneratedKey:
    full_key: str  # shown to the caller exactly once
    prefix: str  # stored, used for the DB lookup
    key_hash: str  # stored, HMAC-SHA256 of the secret part


def _pepper() -> bytes:
    if not settings.key_pepper:
        raise RuntimeError("KEY_PEPPER is not configured")
    return settings.key_pepper.encode()


def hash_secret(secret: str, pepper: bytes | None = None) -> str:
    key = pepper if pepper is not None else _pepper()
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


def generate_key() -> GeneratedKey:
    prefix = secrets.token_hex(PREFIX_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return GeneratedKey(
        full_key=f"{KEY_TAG}_{prefix}_{secret}",
        prefix=prefix,
        key_hash=hash_secret(secret),
    )


def parse_key(raw: str) -> tuple[str, str] | None:
    """Split a raw key into (prefix, secret), or return None if malformed."""
    tag, sep1, rest = raw.partition("_")
    prefix, sep2, secret = rest.partition("_")
    if tag != KEY_TAG or not (sep1 and sep2 and prefix and secret):
        return None
    return prefix, secret


def verify_key(raw: str, stored_hash: str) -> bool:
    """Constant-time check of a raw key against a stored hash."""
    parsed = parse_key(raw)
    if parsed is None:
        return False
    _, secret = parsed
    return hmac.compare_digest(hash_secret(secret), stored_hash)
