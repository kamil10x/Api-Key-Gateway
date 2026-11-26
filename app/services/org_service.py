import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import Org
from app.schemas.admin import OrgCreate
from app.services import plan_service


def create_org(db: Session, data: OrgCreate) -> Org:
    plan = plan_service.get_plan(db, data.plan_id)  # 404 if the plan is unknown
    org = Org(name=data.name, plan_id=plan.id)
    db.add(org)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "org_name_taken", f"An organization named '{data.name}' already exists"
        ) from exc
    db.refresh(org)
    return org


def get_org(db: Session, org_id: uuid.UUID) -> Org:
    org = db.get(Org, org_id)
    if org is None:
        raise NotFoundError("org_not_found", "Organization does not exist")
    return org


def list_orgs(db: Session, limit: int, offset: int) -> list[Org]:
    stmt = select(Org).order_by(Org.created_at, Org.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))
