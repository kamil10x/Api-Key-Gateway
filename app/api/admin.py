import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.data.db import get_db
from app.models import Org, Plan
from app.schemas.admin import (
    KeyCreate,
    KeyCreated,
    OrgCreate,
    OrgRead,
    PlanCreate,
    PlanRead,
)
from app.services import key_service, org_service, plan_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/plans", response_model=PlanRead, status_code=201)
def create_plan(payload: PlanCreate, db: DbSession) -> Plan:
    return plan_service.create_plan(db, payload)


@router.get("/plans", response_model=list[PlanRead])
def list_plans(db: DbSession, limit: Limit = 50, offset: Offset = 0) -> list[Plan]:
    return plan_service.list_plans(db, limit, offset)


@router.post("/orgs", response_model=OrgRead, status_code=201)
def create_org(payload: OrgCreate, db: DbSession) -> Org:
    return org_service.create_org(db, payload)


@router.get("/orgs", response_model=list[OrgRead])
def list_orgs(db: DbSession, limit: Limit = 50, offset: Offset = 0) -> list[Org]:
    return org_service.list_orgs(db, limit, offset)


@router.post("/orgs/{org_id}/keys", response_model=KeyCreated, status_code=201)
def create_key(
    org_id: uuid.UUID,
    response: Response,
    db: DbSession,
    payload: KeyCreate | None = None,
) -> KeyCreated:
    key, full_key = key_service.create_key(db, org_id, payload.label if payload else None)
    response.headers["Cache-Control"] = "no-store"  # the one-time secret must not be cached
    return KeyCreated(
        key_id=key.id,
        api_key=full_key,
        prefix=key.prefix,
        org_id=key.org_id,
        label=key.label,
        created_at=key.created_at,
    )
