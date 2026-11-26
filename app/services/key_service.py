import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.keys import generate_key
from app.models import ApiKey
from app.services import org_service

_MAX_ATTEMPTS = 3


def create_key(db: Session, org_id: uuid.UUID, label: str | None) -> tuple[ApiKey, str]:
    """Create a key and return (row, plaintext). The plaintext exists only in
    this return value: it is never stored or logged."""
    org = org_service.get_org(db, org_id)
    if not org.is_active:
        raise ConflictError("org_inactive", "Cannot create keys for an inactive organization")
    org_pk = org.id

    for _ in range(_MAX_ATTEMPTS):  # a prefix collision (48 bits) is vanishingly rare
        generated = generate_key()
        key = ApiKey(
            org_id=org_pk,
            prefix=generated.prefix,
            key_hash=generated.key_hash,
            label=label,
        )
        db.add(key)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(key)
        return key, generated.full_key

    raise RuntimeError("could not allocate a unique key prefix")
