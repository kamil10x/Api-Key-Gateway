import pytest
from sqlalchemy import inspect

from app.core import keys
from app.models.api_key import ApiKey  # adjust to your model path


@pytest.fixture(autouse=True)
def _pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "_pepper", lambda: b"test-pepper")


def test_generated_keys_are_unique() -> None:
    assert len({keys.generate_key().full_key for _ in range(200)}) == 200


def test_roundtrip_verify() -> None:
    k = keys.generate_key()
    assert keys.verify_key(k.full_key, k.key_hash)


def test_wrong_secret_rejected() -> None:
    k = keys.generate_key()
    other = keys.generate_key()
    assert not keys.verify_key(other.full_key, k.key_hash)


@pytest.mark.parametrize("bad", ["", "qk_", "qk_abc", "xx_abc_def", "garbage"])
def test_malformed_rejected(bad: str) -> None:
    assert not keys.verify_key(bad, "0" * 64)


def test_different_pepper_gives_different_hash() -> None:
    assert keys.hash_secret("s", b"a") != keys.hash_secret("s", b"b")


def test_hash_is_not_the_secret() -> None:
    k = keys.generate_key()
    parsed = keys.parse_key(k.full_key)
    assert parsed is not None
    _, secret = parsed
    assert secret not in k.key_hash
    assert k.key_hash != secret


def test_db_has_no_column_for_plaintext_secret() -> None:
    cols = {c.key for c in inspect(ApiKey).columns}
    assert not cols & {"secret", "api_key", "full_key", "key"}
    assert "key_hash" in cols
