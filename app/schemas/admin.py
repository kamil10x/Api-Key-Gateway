import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

INT32_MAX = 2_147_483_647  # plan columns are 32-bit integers


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PlanCreate(_Request):
    name: str = Field(min_length=1, max_length=64)
    rate_limit_per_second: int = Field(gt=0, le=INT32_MAX)
    burst: int = Field(gt=0, le=INT32_MAX)
    monthly_quota: int = Field(gt=0, le=INT32_MAX)

    @model_validator(mode="after")
    def _burst_covers_rate(self) -> Self:
        # Mirrors ck_plans_burst_gte_rate so the client gets a 422, not a DB error.
        if self.burst < self.rate_limit_per_second:
            raise ValueError("burst must be >= rate_limit_per_second")
        return self


class PlanRead(_Response):
    id: uuid.UUID
    name: str
    rate_limit_per_second: int
    burst: int
    monthly_quota: int
    created_at: datetime


class OrgCreate(_Request):
    name: str = Field(min_length=1, max_length=128)
    plan_id: uuid.UUID


class OrgRead(_Response):
    id: uuid.UUID
    name: str
    plan_id: uuid.UUID
    is_active: bool
    created_at: datetime


class KeyCreate(_Request):
    label: str | None = Field(default=None, min_length=1, max_length=128)


class KeyCreated(BaseModel):
    """The only response that ever contains the plaintext key."""

    key_id: uuid.UUID
    api_key: str
    prefix: str
    org_id: uuid.UUID
    label: str | None
    created_at: datetime
