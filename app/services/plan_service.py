import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import Plan
from app.schemas.admin import PlanCreate


def create_plan(db: Session, data: PlanCreate) -> Plan:
    plan = Plan(**data.model_dump())
    db.add(plan)
    try:
        db.commit()
    except IntegrityError as exc:
        # The schema already enforces the CHECK constraints, so the only
        # constraint that can fire here is the unique plan name.
        db.rollback()
        raise ConflictError(
            "plan_name_taken", f"A plan named '{data.name}' already exists"
        ) from exc
    db.refresh(plan)
    return plan


def get_plan(db: Session, plan_id: uuid.UUID) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise NotFoundError("plan_not_found", "Plan does not exist")
    return plan


def list_plans(db: Session, limit: int, offset: int) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.created_at, Plan.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))
