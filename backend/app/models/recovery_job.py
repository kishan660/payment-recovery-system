from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime

from app.database.database import Base


class RecoveryJob(Base):
    __tablename__ = "recovery_jobs"

    __table_args__ = (
        UniqueConstraint(
            "payment_id",
            name="uq_recovery_job_payment"
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    payment_id = Column(
        String,
        nullable=False,
        index=True
    )

    status = Column(
        String,
        default="QUEUED",
        nullable=False
    )

    retry_count = Column(
        Integer,
        default=0,
        nullable=False
    )

    max_retries = Column(
        Integer,
        default=3,
        nullable=False
    )

    next_retry_at = Column(
        DateTime,
        nullable=True
    )

    last_error = Column(
        String,
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )