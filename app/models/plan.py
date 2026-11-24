from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.org import Org


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("rate_limit_per_second > 0", name="rate_limit_positive"),
        CheckConstraint("burst >= rate_limit_per_second", name="burst_gte_rate"),
        CheckConstraint("monthly_quota > 0", name="quota_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    rate_limit_per_second: Mapped[int]
    burst: Mapped[int]
    monthly_quota: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    orgs: Mapped[list[Org]] = relationship(back_populates="plan")